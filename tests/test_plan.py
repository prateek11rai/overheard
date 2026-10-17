from datetime import date

from overheard.pipeline import PlanInput, days_left_in_month, make_plan
from overheard.sources import SerpApiAccount

TODAY = date(2026, 10, 18)  # 14 days left in October, today included


def products():
    return [
        PlanInput(1, "mealwise", "Mealwise", ["a1", "a2", "a3", "a4", "a5"], reddit=True),
        PlanInput(2, "shipnote", "Shipnote", ["b1", "b2"], reddit=True),
        PlanInput(3, "hnonly", "HN Only", ["c1"], reddit=False),
    ]


def test_days_left():
    assert days_left_in_month(TODAY) == 14
    assert days_left_in_month(date(2026, 10, 31)) == 1


def test_no_key_means_hacker_news_only():
    plan = make_plan(products(), None, TODAY)
    assert plan.mode == "off"
    assert plan.reddit_searches == 0
    assert [p.hn_phrases for p in plan.products] == [
        ["a1", "a2", "a3", "a4", "a5"],
        ["b1", "b2"],
        ["c1"],
    ]


def test_fits_searches_every_phrase():
    # 250 plan, 10% reserve = 25; 135 left - 25 = 110 over 14 days = 7 a day; 7 phrases
    acc = SerpApiAccount(plan_searches=250, searches_left=135, this_month_usage=115)
    plan = make_plan(products(), acc, TODAY)
    assert plan.mode == "every"
    assert plan.per_day == 7
    assert plan.reddit_searches == 7
    assert plan.products[0].reddit_phrases == ["a1", "a2", "a3", "a4", "a5"]
    assert plan.products[2].reddit_phrases == []
    assert plan.products[2].hn_phrases == ["c1"]


def test_takes_turns_least_recently_searched_first():
    # 25 + 4*14 = 81 left -> 56 usable / 14 days = 4 a day for 7 phrases
    acc = SerpApiAccount(plan_searches=250, searches_left=81, this_month_usage=169)
    last = {
        (1, "a1"): date(2026, 10, 17),
        (1, "a2"): date(2026, 10, 17),
        (2, "b1"): date(2026, 10, 17),
        (1, "a3"): date(2026, 10, 16),
    }
    plan = make_plan(products(), acc, TODAY, last_searched=last)
    assert plan.mode == "rotate"
    assert plan.per_day == 4
    chosen = {ph for p in plan.products for ph in p.reddit_phrases}
    # never searched first (a4, a5, b2), then the oldest (a3)
    assert chosen == {"a4", "a5", "b2", "a3"}
    assert plan.products[0].every_days == 2
    assert plan.products[0].reddit_note == "phrases checked every 2 days"
    assert plan.reddit_searches == 4


def test_takes_turns_alternates_products_when_none_searched():
    acc = SerpApiAccount(plan_searches=250, searches_left=25 + 2 * 14, this_month_usage=0)
    plan = make_plan(products(), acc, TODAY)
    assert plan.per_day == 2
    assert plan.products[0].reddit_phrases == ["a1"]
    assert plan.products[1].reddit_phrases == ["b1"]


def test_daily_cap_limits_searches():
    acc = SerpApiAccount(plan_searches=250, searches_left=250, this_month_usage=0)
    plan = make_plan(products(), acc, TODAY, daily_cap=3, used_today=1)
    assert plan.per_day == 2
    assert plan.reddit_searches == 2
    assert plan.mode == "rotate"


def test_monthly_cap_limits_searches():
    acc = SerpApiAccount(plan_searches=250, searches_left=250, this_month_usage=0)
    plan = make_plan(products(), acc, TODAY, monthly_cap=100, used_month=72)
    # min(250 - 25, 100 - 72) = 28 over 14 days = 2 a day
    assert plan.per_day == 2


def test_used_up_plan_falls_back_to_hacker_news():
    acc = SerpApiAccount(plan_searches=250, searches_left=20, this_month_usage=230)
    plan = make_plan(products(), acc, TODAY)
    assert plan.mode == "used_up"
    assert plan.reddit_searches == 0
    assert plan.products[0].hn_phrases == ["a1", "a2", "a3", "a4", "a5"]


def test_today_follows_the_schedule_timezone_across_a_month_boundary():
    from datetime import UTC, datetime

    from overheard.pipeline import today_in

    evening_utc = datetime(2026, 10, 31, 20, 0, tzinfo=UTC)
    assert today_in("UTC", evening_utc) == date(2026, 10, 31)
    assert today_in("Asia/Kolkata", evening_utc) == date(2026, 11, 1)


def test_one_budget_function_at_a_month_boundary():
    from overheard.pipeline import reddit_budget

    acc = SerpApiAccount(plan_searches=250, searches_left=40, this_month_usage=210)
    last_day = reddit_budget(acc, date(2026, 10, 31))
    assert (last_day.days_left, last_day.reserve, last_day.per_day) == (1, 25, 15)
    manual = reddit_budget(acc, date(2026, 10, 31), scheduled=False)
    assert (manual.reserve, manual.per_day) == (0, 40)
    fresh = SerpApiAccount(plan_searches=250, searches_left=250, this_month_usage=0)
    first_day = reddit_budget(fresh, date(2026, 11, 1))
    assert (first_day.days_left, first_day.month_days, first_day.per_day) == (30, 30, 7)
    assert first_day.projected(7) == 210
    assert first_day.fits(7) and not first_day.fits(8)
    assert reddit_budget(None, date(2026, 11, 1)) is None


def test_the_reserve_is_for_scheduled_runs_only():
    acc = SerpApiAccount(plan_searches=250, searches_left=27, this_month_usage=223)
    scheduled = make_plan(products(), acc, date(2026, 10, 31))
    assert scheduled.mode == "rotate" and scheduled.per_day == 2
    manual = make_plan(products(), acc, date(2026, 10, 31), scheduled=False)
    assert manual.mode == "every" and manual.per_day == 27 and manual.reddit_searches == 7


def test_phrase_cost_is_shared():
    from overheard.pipeline import phrase_cost

    assert phrase_cost(["a", "b", "c"], reddit_on=True, has_key=True) == 3
    assert phrase_cost(["a", "b", "c"], reddit_on=False, has_key=True) == 0
    assert phrase_cost(["a", "b", "c"], reddit_on=True, has_key=False) == 0


def test_phrases_already_paid_today_are_free_and_never_rationed():
    # cap 60 with 8 spent: 52 over 14 days = 3 a day, but 7 phrases were all searched today
    acc = SerpApiAccount(plan_searches=250, searches_left=241, this_month_usage=9)
    paid = {(1, p): TODAY for p in ["a1", "a2", "a3", "a4", "a5"]} | {(2, "b1"): TODAY}
    plan = make_plan(
        products(), acc, TODAY, used_month=8, monthly_cap=60, last_searched=paid, scheduled=False
    )
    assert plan.per_day == 3
    assert plan.mode == "every"  # only b2 is unpaid, and it fits
    assert plan.products[0].reddit_phrases == ["a1", "a2", "a3", "a4", "a5"]
    assert plan.products[1].reddit_phrases == ["b1", "b2"]
    paid[(2, "b2")] = TODAY
    plan = make_plan(
        products(), acc, TODAY, used_month=8, monthly_cap=60, last_searched=paid, scheduled=False
    )
    assert plan.mode == "every"
    assert plan.reddit_searches == 7


def test_only_unpaid_phrases_take_turns():
    acc = SerpApiAccount(plan_searches=250, searches_left=241, this_month_usage=9)
    yesterday = date(2026, 10, 17)
    seen = {(1, "a1"): TODAY, (1, "a2"): TODAY, (1, "a3"): yesterday}
    plan = make_plan(
        products(), acc, TODAY, used_month=46, monthly_cap=60, last_searched=seen, scheduled=False
    )
    assert plan.per_day == 1  # 14 left over 14 days
    chosen = {(p.id, ph) for p in plan.products for ph in p.reddit_phrases}
    assert {(1, "a1"), (1, "a2")} <= chosen
    assert len(chosen) == 3 and (1, "a3") not in chosen
