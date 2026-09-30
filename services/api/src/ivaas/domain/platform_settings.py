"""The handful of settings that belong to the business rather than the deployment.

Most of what configures this platform is infrastructure: database URLs, model paths,
object storage. Those change when someone redeploys it, and a screen that pretended
otherwise would be lying. These three are different. They are decisions a site
manager makes and revisits, they need no restart, and they change what the system
does with a truck.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RECONCILE_TOLERANCE = "reconcile_tolerance"
AUTO_CLOSE_IDLE_MINUTES = "auto_close_idle_minutes"
AUTO_OPEN_DIRECTION = "auto_open_direction"
FACE_RECOGNITION = "face_recognition"
FACE_RECOGNITION_BASIS = "face_recognition_basis"
BADGE_GRACE_MINUTES = "badge_grace_minutes"
EVIDENCE_RETENTION_DAYS = "evidence_retention_days"

#: the value a setting has when nobody has changed it (the environment supplies the rest)
DEFAULTS = {
    FACE_RECOGNITION: "off",
    FACE_RECOGNITION_BASIS: "",
    BADGE_GRACE_MINUTES: 10.0,
    # confirmed for Bakers Inn: crate-leakage disputes are raised within 90 days
    EVIDENCE_RETENTION_DAYS: 90.0,
}


@dataclass(frozen=True)
class EditableSetting:
    key: str
    label: str
    help: str
    kind: str  # "percent" | "minutes" | "days" | "choice" | "text"
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None


EDITABLE: tuple[EditableSetting, ...] = (
    EditableSetting(
        key=RECONCILE_TOLERANCE,
        label="Counting accuracy target",
        help=(
            "A verified load at or above this accuracy is reconciled; below it the load "
            "is disputed and needs an admin to sign it off."
        ),
        kind="percent",
        minimum=0.5,
        maximum=1.0,
    ),
    EditableSetting(
        key=AUTO_CLOSE_IDLE_MINUTES,
        label="Close a load after",
        help=(
            "A load whose number plate has not been seen for this long is closed "
            "automatically, on the assumption the truck has left. 0 disables it."
        ),
        kind="minutes",
        minimum=0,
        maximum=240,
    ),
    EditableSetting(
        key=AUTO_OPEN_DIRECTION,
        label="A plate at an idle bay opens",
        help="What to assume a truck is doing when the LPR camera reads its plate.",
        kind="choice",
        choices=("loading", "offloading", ""),
    ),
    EditableSetting(
        key=BADGE_GRACE_MINUTES,
        label="A badge-in admits someone for",
        help=(
            "Someone seen in a badge-required zone is accounted for if the zone's door "
            "logged a granted swipe within this many minutes before."
        ),
        kind="minutes",
        minimum=1,
        maximum=120,
    ),
    EditableSetting(
        key=EVIDENCE_RETENTION_DAYS,
        label="Evidence clips are kept for",
        help=(
            "Video of each counted load, kept so a disputed count can be checked against "
            "what the camera saw. Clips older than this are deleted automatically."
        ),
        kind="days",
        minimum=7,
        maximum=3650,
    ),
    EditableSetting(
        key=FACE_RECOGNITION_BASIS,
        label="Legal basis for face recognition",
        help=(
            "Where the lawful basis for processing staff faces is recorded: the consent "
            "process, the data protection assessment, the regulator's reference. Face "
            "recognition cannot be switched on until this is filled in."
        ),
        kind="text",
        maximum=500,
    ),
    EditableSetting(
        key=FACE_RECOGNITION,
        label="Face recognition",
        help=(
            "Recognise enrolled people in 'known faces only' zones. It processes biometric "
            "data: switch it on only with the legal basis above. Switching it off stops "
            "recognition at once; enrolled faces are kept until removed."
        ),
        kind="choice",
        choices=("off", "on"),
    ),
)

_BY_KEY = {s.key: s for s in EDITABLE}


class InvalidSettingError(ValueError):
    pass


def validate(key: str, value: Any) -> Any:
    """Coerce and bounds-check one setting. Raises InvalidSettingError if it cannot."""
    spec = _BY_KEY.get(key)
    if spec is None:
        raise InvalidSettingError(f"{key} is not an editable setting")
    if spec.kind == "text":
        text = str(value or "").strip()
        if spec.maximum is not None and len(text) > spec.maximum:
            raise InvalidSettingError(f"{key} must be at most {int(spec.maximum)} characters")
        return text
    if spec.kind == "choice":
        if value not in spec.choices:
            allowed = ", ".join(repr(c) for c in spec.choices)
            raise InvalidSettingError(f"{key} must be one of {allowed}")
        return value
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidSettingError(f"{key} must be a number") from exc
    if spec.minimum is not None and number < spec.minimum:
        raise InvalidSettingError(f"{key} must be at least {spec.minimum}")
    if spec.maximum is not None and number > spec.maximum:
        raise InvalidSettingError(f"{key} must be at most {spec.maximum}")
    return number
