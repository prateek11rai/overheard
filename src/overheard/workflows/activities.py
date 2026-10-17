from overheard.workflows.activities_base import heartbeating
from overheard.workflows.modelwork import ModelActivities
from overheard.workflows.searching import SearchActivities

__all__ = ["Activities", "heartbeating"]


class Activities(SearchActivities, ModelActivities):
    """The morning job's activities, sharing one database pool."""

    def all(self) -> list:
        """Every activity, for the worker.

        Returns:
            Activity callables.
        """
        return [
            self.plan_morning,
            self.plan_check,
            self.search_reddit,
            self.search_hn,
            self.save_posts,
            self.label_posts,
            self.update_themes,
            self.send_digest,
            self.record_step,
            self.finish_run,
            self.reembed,
            self.compare_recent,
        ]
