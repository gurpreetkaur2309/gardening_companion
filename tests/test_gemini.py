"""Gemini integration: structured output, caching, and graceful degradation."""

from __future__ import annotations


def test_care_lookup_parses_structured_json(app, fake_gemini):
    from app.services import gemini_service

    with app.app_context():
        result = gemini_service.infer_care_schedule("Monstera")

    assert result.succeeded is True
    assert result.care.watering_days == 5
    assert result.care.fertilizing_days == 20
    assert result.care.sunlight == "full"


def test_malformed_json_falls_back_instead_of_raising(app, fake_gemini):
    """The old parser raised KeyError here and surfaced 'Error adding plant'."""
    from app.services import gemini_service

    fake_gemini.next_response = "Watering: whenever you feel like it 🌿"

    with app.app_context():
        result = gemini_service.infer_care_schedule("Nonsense plant")

    assert result.succeeded is False
    assert result.care == gemini_service.CONSERVATIVE_CARE


def test_out_of_range_numbers_are_rejected(app, fake_gemini):
    from app.services import gemini_service

    fake_gemini.next_response = (
        '{"watering_days": 0, "fertilizing_days": 30, "sunlight": "full", '
        '"fertilizer_type": "x", "notes": ""}'
    )
    with app.app_context():
        assert gemini_service.infer_care_schedule("Zero plant").succeeded is False


def test_unknown_sunlight_value_is_normalised(app, fake_gemini):
    from app.services import gemini_service

    fake_gemini.next_response = (
        '{"watering_days": 7, "fertilizing_days": 30, "sunlight": "moonlight", '
        '"fertilizer_type": "x", "notes": ""}'
    )
    with app.app_context():
        result = gemini_service.infer_care_schedule("Moon plant")

    assert result.succeeded is True
    assert result.care.sunlight == "partial"


def test_no_api_key_means_conservative_defaults_not_a_crash(app):
    from app.services import gemini_service

    app.extensions["gemini"] = None
    with app.app_context():
        result = gemini_service.infer_care_schedule("Anything")
        assert gemini_service.is_available() is False

    assert result.succeeded is False
    assert result.care.watering_days == 7


def test_chat_without_a_key_explains_itself(app):
    from app.services import gemini_service

    app.extensions["gemini"] = None
    with app.app_context():
        reply = gemini_service.chat("How often should I water basil?")

    assert "isn't configured" in reply


# --- Caching ---------------------------------------------------------------
def test_an_ai_schedule_is_cached_onto_the_species(app, user, garden, make_plant, fake_gemini):
    """The cost lever: each species should cost one API call, ever."""
    from app.models.enums import CareSource
    from app.repositories import plant_repo
    from app.services import garden_service

    uncatalogued = make_plant(care_source=CareSource.DEFAULT)

    with app.app_context():
        garden_service.add_plant(
            user_id=user.id, garden_id=garden.id, plant_id=uncatalogued.id
        )
        assert len(fake_gemini.calls) == 1

        refreshed = plant_repo.get(uncatalogued.id)
        assert refreshed.care_source == CareSource.AI
        assert refreshed.care.watering_days == 5


def test_a_cached_species_never_calls_the_api_again(app, make_user, make_plant, fake_gemini):
    from app.models.enums import CareSource
    from app.services import garden_service

    uncatalogued = make_plant(care_source=CareSource.DEFAULT)
    first = make_user(email="f@example.com", username="firstuser")
    second = make_user(email="s@example.com", username="seconduser")

    with app.app_context():
        g1 = garden_service.ensure_default_garden(first.id)
        garden_service.add_plant(
            user_id=first.id, garden_id=g1.id, plant_id=uncatalogued.id
        )
        calls_after_first = len(fake_gemini.calls)

        g2 = garden_service.ensure_default_garden(second.id)
        garden_service.add_plant(
            user_id=second.id, garden_id=g2.id, plant_id=uncatalogued.id
        )

    assert calls_after_first == 1
    assert len(fake_gemini.calls) == 1  # the second user paid nothing


def test_curated_species_never_reach_the_api(app, user, garden, plant, fake_gemini):
    from app.services import garden_service

    with app.app_context():
        garden_service.add_plant(user_id=user.id, garden_id=garden.id, plant_id=plant.id)

    assert fake_gemini.calls == []


# --- Context ---------------------------------------------------------------
def test_chat_context_names_the_users_real_plants(app, user, overdue_planting):
    from app.services import care_service

    with app.app_context():
        context = care_service.build_chat_context(user.id, "Gurpreet")

    assert "Gurpreet" in context
    assert "My Garden" in context
    assert "Overdue right now:" in context
    assert "nothing" not in context.split("Overdue right now:")[1].split("\n")[0]


def test_chat_context_of_an_empty_account_says_so(app, user):
    from app.services import care_service

    with app.app_context():
        context = care_service.build_chat_context(user.id, "Nobody")

    assert "none added yet" in context
