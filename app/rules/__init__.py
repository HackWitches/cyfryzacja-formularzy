"""Deterministic Polish paper-form rules, applied to the model's contract before the builder.

The model guesses; the rules fix what the paper states plainly (types, optional fields, conditions, addressee).
A rule is a function `rule(contract, ctx) -> notes` that edits the contract in place and returns debug notes.
A rule stays in RULES only if it improves the measured results (README, "Reguły PL").
"""

from app.rules import cleanup, cues, field_types, header, visibility
from app.rules.text import Context

RULES = [  # order matters: clean titles first, types before visibility, visibility before required
    ("clean_titles", cleanup.clean_titles),
    ("drop_office_fields", cleanup.drop_office_fields),
    ("drop_duplicates", cleanup.drop_duplicates),
    ("statements_to_consents", cleanup.statements_to_consents),
    ("type_from_label", field_types.type_from_label),
    ("type_from_cells", field_types.type_from_cells),
    ("country_dictionary", field_types.country_dictionary),
    ("multiple_choice", field_types.multiple_choice),
    ("condition_other_or_yes", visibility.condition_other_or_yes),
    ("condition_from_cue", cues.condition_from_cue),
    ("twin_sections", visibility.twin_sections),
    ("required", visibility.required),
    ("entity_variant", header.entity_variant),
    ("addressee", header.addressee),
    ("drop_base_person_fields", cleanup.drop_base_person_fields),  # last: reads the final types
]


def apply(contract: dict, ctx: Context, only=None) -> list[str]:
    """Run the rules (all, or the names in `only`) on a normalized contract; returns the notes."""
    notes = []
    for name, rule in RULES:
        if only is None or name in only:
            notes += [f"{name}: {n}" for n in rule(contract, ctx)]
    return notes


__all__ = ["Context", "RULES", "apply"]
