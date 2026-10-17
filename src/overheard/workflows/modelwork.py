import time
from typing import Any

from temporalio import activity, workflow
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from overheard import pipeline
    from overheard.ai import ModelUnavailableError

from overheard.workflows.activities_base import ActivitiesBase, heartbeating
from overheard.workflows.types import (
    ModelArgs,
    ScanArgs,
)


class ModelActivities(ActivitiesBase):
    """Labelling, themes, the digest and model jobs."""

    @activity.defn(name="label_posts")
    async def label_posts(self, args: ScanArgs) -> int:
        """Label new posts with the `label` model and check every quote.

        Args:
            args: The planned product.

        Returns:
            Posts labelled.

        Raises:
            ApplicationError: When the model server can't be reached (retried).
        """
        started = time.monotonic()
        product = await self.db.product(args.product_id)
        if product is None:
            return 0
        posts = await self.db.unlabelled_posts(product.id)
        rechecked = unverified = 0
        async with await self._ai() as ai, heartbeating():
            for i, post in enumerate(posts):
                activity.heartbeat(i)
                try:
                    out = await pipeline.label_post(
                        ai,
                        product,
                        post.title,
                        post.body,
                        post.community,
                        is_comment=post.is_comment,
                    )
                except ModelUnavailableError as exc:
                    raise ApplicationError(str(exc)) from exc
                rechecked += out.attempts > 1
                unverified += out.label == "unverified"
                await self.db.set_label(
                    post.id,
                    out.label,
                    out.quote,
                    out.competitor,
                    ai.model_for("label"),
                    out.attempts,
                )
        text = f"Labelled {len(posts)}"
        if rechecked:
            text += f" · {rechecked} quote{'s' if rechecked != 1 else ''} re-checked"
        if unverified:
            text += f" · {unverified} unverified"
        await self.db.add_step(
            args.run_id,
            f"label:{product.id}",
            text,
            "retried" if rechecked else "ok",
            time.monotonic() - started,
            activity.info().attempt,
            product.id,
        )
        return len(posts)

    @activity.defn(name="update_themes")
    async def update_themes(self, args: ScanArgs) -> int:
        """Embed new posts and put pain points into themes (new themes get a name).

        Args:
            args: The planned product.

        Returns:
            Themes started.
        """
        started = time.monotonic()
        product = await self.db.product(args.product_id)
        if product is None:
            return 0
        new_themes = 0
        async with await self._ai() as ai, heartbeating():
            model = ai.model_for("embed")
            try:
                while batch := await self.db.posts_without_embedding(model, product.id, 32):
                    activity.heartbeat(len(batch))
                    vectors = await ai.embed([pipeline.post_text(p.title, p.body) for p in batch])
                    if vectors:
                        await self.db.ensure_embedding_index(model, len(vectors[0]))
                    for p, v in zip(batch, vectors, strict=True):
                        await self.db.save_embedding(p.id, model, v)
                themes = await self.db.themes_for(product.id, model)
                for post in await self.db.unthemed_pain_posts(product.id, model):
                    activity.heartbeat(post.id)
                    vector = await self.db.embedding(post.id, model)
                    if vector is None:
                        continue
                    hit = pipeline.nearest_theme(vector, themes)
                    if hit is None:
                        name = await pipeline.name_theme(ai, product, [post.quote or post.title])
                        tid = await self.db.create_theme(product.id, name, model, vector)
                        await self.db.add_to_theme(post.id, tid, vector)
                        themes.append(
                            {"id": tid, "name": name, "centroid": vector, "post_count": 1}
                        )
                        new_themes += 1
                    else:
                        theme = hit[0]
                        centroid = pipeline.moved_centroid(
                            theme["centroid"], theme["post_count"], vector
                        )
                        await self.db.add_to_theme(post.id, theme["id"], centroid)
                        theme["centroid"], theme["post_count"] = centroid, theme["post_count"] + 1
            except ModelUnavailableError as exc:
                raise ApplicationError(str(exc)) from exc
        await self.db.add_step(
            args.run_id,
            f"themes:{product.id}",
            f"Themes · {new_themes} new",
            "ok",
            time.monotonic() - started,
            activity.info().attempt,
            product.id,
        )
        return new_themes

    @activity.defn(name="send_digest")
    async def send_digest(self, args: ScanArgs) -> str:
        """Email the product's digest (or write it to a file, or keep it in the app).

        Args:
            args: The planned product.

        Returns:
            What happened, in a few words.
        """
        from overheard import mail

        started = time.monotonic()
        product = await self.db.product(args.product_id)
        if product is None:
            return "product gone"
        tz = await self.db.get_setting("timezone") or self.settings.default_timezone()
        run = await self.db.run(args.run_id) if args.run_id else None
        since = run.started_at if run is not None else None
        brief = await pipeline.brief_for(self.db, product, tz, since=since)
        text = await mail.deliver_once(self.db, self.settings, product, brief, args.run_key, tz=tz)
        await self.db.add_step(
            args.run_id,
            f"email:{product.id}",
            text,
            "ok",
            time.monotonic() - started,
            activity.info().attempt,
            product.id,
        )
        return text

    @activity.defn(name="reembed")
    async def reembed(self, args: ModelArgs) -> int:
        """Embed every stored post with `args.model` and rebuild theme centroids.

        Args:
            args: The embedding model.

        Returns:
            Posts embedded.

        Raises:
            ApplicationError: When the model server can't be reached (retried).
        """
        info = activity.info()
        started = time.monotonic()
        run_id = await self.db.upsert_run(info.workflow_id, "reembed")
        done = 0
        try:
            async with await self._ai() as ai, heartbeating():
                while batch := await self.db.posts_without_embedding(args.model, None, 64):
                    activity.heartbeat(done)
                    vectors = await ai.embed(
                        [pipeline.post_text(p.title, p.body) for p in batch], model=args.model
                    )
                    await self.db.ensure_embedding_index(args.model, len(vectors[0]))
                    for p, v in zip(batch, vectors, strict=True):
                        await self.db.save_embedding(p.id, args.model, v)
                    done += len(batch)
        except ModelUnavailableError as exc:
            raise ApplicationError(str(exc)) from exc
        themes = await self.db.switch_embedding(args.model)
        await self.db.add_step(
            run_id,
            "reembed",
            f"Re-embedded {done} posts with {args.model} · {themes} themes updated · now in use",
            "ok",
            time.monotonic() - started,
            info.attempt,
        )
        return done

    @activity.defn(name="compare_recent")
    async def compare_recent(self, args: ModelArgs) -> dict[str, Any]:
        """Label recent real posts with another model, without changing any label.

        Args:
            args: The model and how many posts.

        Returns:
            Agreement numbers.
        """
        from overheard import evals

        info = activity.info()
        run_id = await self.db.upsert_run(info.workflow_id, "compare")
        async with await self._ai() as ai, heartbeating():
            result = await evals.compare_recent(
                self.db, ai, args.model, args.limit, heartbeat=activity.heartbeat
            )
        await self.db.add_step(
            run_id,
            "compare",
            f"{args.model} agreed on {result['agree']} of {result['items']} recent posts",
            "ok",
            result["seconds"],
            info.attempt,
        )
        return result
