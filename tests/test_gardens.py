"""Garden and planting behaviour."""

from __future__ import annotations

import pytest

from app.errors import ConflictError, NotFoundError, ValidationError
from app.models import AgeStage


def test_first_visit_creates_a_default_garden(app, user):
    from app.services import garden_service

    with app.app_context():
        garden = garden_service.ensure_default_garden(user.id)
        assert garden.name == "My Garden"
        # Calling again returns the same one rather than making a second.
        assert garden_service.ensure_default_garden(user.id).id == garden.id


def test_duplicate_garden_names_are_rejected_per_user(app, user):
    from app.services import garden_service

    with app.app_context():
        garden_service.create_garden(user.id, "Balcony")
        with pytest.raises(ConflictError):
            garden_service.create_garden(user.id, "Balcony")


def test_two_users_may_each_have_a_balcony(app, make_user):
    from app.services import garden_service

    a = make_user(email="a@example.com", username="usera")
    b = make_user(email="b@example.com", username="userb")
    with app.app_context():
        garden_service.create_garden(a.id, "Balcony")
        garden_service.create_garden(b.id, "Balcony")  # must not raise


def test_the_last_garden_cannot_be_deleted(app, user, garden):
    from app.services import garden_service

    with app.app_context(), pytest.raises(ValidationError, match="only garden"):
        garden_service.delete_garden(garden.id, user.id)


def test_deleting_a_garden_removes_its_plantings_and_logs(app, user, garden, plant):
    from app.models import CareAction
    from app.repositories import care_log_repo, garden_plant_repo
    from app.services import care_service, garden_service

    with app.app_context():
        extra = garden_service.create_garden(user.id, "Terrace")
        planting, _ = garden_service.add_plant(
            user_id=user.id, garden_id=extra.id, plant_id=plant.id
        )
        care_service.record_care(planting.id, user.id, CareAction.WATER)

        removed = garden_service.delete_garden(extra.id, user.id)

        assert removed == 1
        assert garden_plant_repo.get(planting.id, user.id) is None
        assert care_log_repo.list_for_planting(planting.id) == []


# --- Adding plants ---------------------------------------------------------
def test_adding_the_same_species_and_stage_merges_into_one_card(app, user, garden, plant):
    from app.repositories import garden_plant_repo
    from app.services import garden_service

    with app.app_context():
        first, created_first = garden_service.add_plant(
            user_id=user.id, garden_id=garden.id, plant_id=plant.id
        )
        second, created_second = garden_service.add_plant(
            user_id=user.id, garden_id=garden.id, plant_id=plant.id, quantity=2
        )

    assert created_first is True
    assert created_second is False
    assert first.id == second.id
    assert second.quantity == 3
    with app.app_context():
        assert len(garden_plant_repo.list_for_garden(garden.id, user.id)) == 1


def test_different_life_stages_stay_separate(app, user, garden, plant):
    """A seedling and a mature specimen need different schedules."""
    from app.repositories import garden_plant_repo
    from app.services import garden_service

    with app.app_context():
        garden_service.add_plant(
            user_id=user.id, garden_id=garden.id, plant_id=plant.id,
            age_stage=AgeStage.SEEDLING,
        )
        garden_service.add_plant(
            user_id=user.id, garden_id=garden.id, plant_id=plant.id,
            age_stage=AgeStage.MATURE,
        )
        assert len(garden_plant_repo.list_for_garden(garden.id, user.id)) == 2


def test_a_nicknamed_plant_gets_its_own_card(app, user, garden, plant):
    from app.repositories import garden_plant_repo
    from app.services import garden_service

    with app.app_context():
        garden_service.add_plant(user_id=user.id, garden_id=garden.id, plant_id=plant.id)
        garden_service.add_plant(
            user_id=user.id, garden_id=garden.id, plant_id=plant.id, nickname="Big Bertha"
        )
        assert len(garden_plant_repo.list_for_garden(garden.id, user.id)) == 2


def test_adding_an_unknown_species_fails(app, user, garden):
    from bson import ObjectId

    from app.services import garden_service

    with app.app_context(), pytest.raises(NotFoundError):
        garden_service.add_plant(
            user_id=user.id, garden_id=garden.id, plant_id=ObjectId()
        )


def test_seedling_inherits_a_shorter_interval(app, user, garden, make_plant):
    from app.services import garden_service

    slow = make_plant(watering_days=20)
    with app.app_context():
        planting, _ = garden_service.add_plant(
            user_id=user.id, garden_id=garden.id, plant_id=slow.id,
            age_stage=AgeStage.SEEDLING,
        )
    assert planting.schedule.watering_days == 10


# --- Removing --------------------------------------------------------------
def test_removing_one_of_several_decrements_the_count(app, user, garden, plant):
    from app.services import garden_service

    with app.app_context():
        planting, _ = garden_service.add_plant(
            user_id=user.id, garden_id=garden.id, plant_id=plant.id, quantity=3
        )
        assert garden_service.remove_plant(planting.id, user.id) == 2


def test_removing_the_last_one_deletes_the_planting(app, user, garden, plant):
    from app.repositories import garden_plant_repo
    from app.services import garden_service

    with app.app_context():
        planting, _ = garden_service.add_plant(
            user_id=user.id, garden_id=garden.id, plant_id=plant.id
        )
        assert garden_service.remove_plant(planting.id, user.id) is None
        assert garden_plant_repo.get(planting.id, user.id) is None


# --- Editing ---------------------------------------------------------------
def test_changing_life_stage_rescales_the_schedule(app, user, garden, make_plant):
    from app.services import garden_service

    source = make_plant(watering_days=20)
    with app.app_context():
        planting, _ = garden_service.add_plant(
            user_id=user.id, garden_id=garden.id, plant_id=source.id,
            age_stage=AgeStage.MATURE,
        )
        assert planting.schedule.watering_days == 20

        updated = garden_service.update_planting(
            planting.id, user.id, {"age_stage": str(AgeStage.SEEDLING)}
        )
        assert updated.schedule.watering_days == 10


def test_a_manual_interval_overrides_the_catalog(app, user, planting):
    from app.services import garden_service

    with app.app_context():
        updated = garden_service.update_planting(
            planting.id, user.id, {"watering_days": 3}
        )
        assert updated.schedule.watering_days == 3


def test_manual_interval_is_still_clamped(app, user, planting):
    from app.services import garden_service

    with app.app_context():
        updated = garden_service.update_planting(
            planting.id, user.id, {"watering_days": 5000}
        )
        assert updated.schedule.watering_days == 120


def test_resolve_active_garden_survives_a_bogus_id(app, user, garden):
    from app.services import garden_service

    with app.app_context():
        active, all_gardens = garden_service.resolve_active_garden(user.id, "not-an-objectid")
        assert active.id == garden.id
        assert len(all_gardens) == 1
