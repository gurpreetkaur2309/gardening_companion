"""Bulk catalog import must never clobber curated data."""

from __future__ import annotations

from app.repositories import plant_repo
from app.services import catalog_service

NEW = {
    "common_name": "Yarrow",
    "scientific_name": "Achillea millefolium",
    "family": "Asteraceae",
    "genus": "Achillea",
    "description": "A flowering plant in the family Asteraceae.",
    "image_key": "https://upload.wikimedia.org/x.jpg",
    "care": None,
    "care_source": "default",
}


def test_new_species_are_created_without_care_so_ai_can_fill_it_later(app):
    with app.app_context():
        assert catalog_service.import_records([NEW]) == (1, 0, 0)
        plant = plant_repo.get_by_common_name("Yarrow")
    assert plant is not None and plant.has_care_data is False


def test_reimport_is_idempotent(app):
    with app.app_context():
        catalog_service.import_records([NEW])
        assert catalog_service.import_records([NEW]) == (0, 0, 1)


def test_existing_curated_care_and_edits_are_never_overwritten(app, make_plant):
    with app.app_context():
        curated = make_plant(
            common_name="Yarrow",
            scientific_name="Achillea millefolium",
            description="Hand-written description.",
        )
        before_care = curated.care.to_doc()
        catalog_service.import_records([{**NEW, "care": {"watering_days": 1}, "care_source": "ai"}])
        after = plant_repo.get(curated.id)
    assert after.description == "Hand-written description."
    assert after.care.to_doc() == before_care
    assert after.care_source == curated.care_source


def test_blank_fields_on_existing_species_are_filled(app, make_plant):
    with app.app_context():
        existing = make_plant(
            common_name="Yarrow",
            scientific_name="Achillea millefolium",
            family=None,
            image_key=None,
        )
        assert catalog_service.import_records([NEW]) == (0, 1, 0)
        after = plant_repo.get(existing.id)
    assert after.family == "Asteraceae" and after.image_key == NEW["image_key"]
