from datetime import datetime
import re
import ntpath
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator


class EnrollRequest(BaseModel):
    code: str = Field(min_length=8, max_length=8)
    device_id: str = Field(min_length=1, max_length=80)


class Credentials(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=10, max_length=128)


class PairingCodeRequest(BaseModel):
    child_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    child_name: str = Field(min_length=1, max_length=80)


class HeartbeatRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=80)
    child_id: str = Field(min_length=1, max_length=80)
    policy_version: int = Field(ge=0)


class PolicyRequest(BaseModel):
    weekday_quota_min: int = Field(ge=1, le=1440)
    weekend_quota_min: int = Field(ge=1, le=1440)
    timezone: str = Field(default="Asia/Bangkok", min_length=1, max_length=64)
    # Monday through Sunday; 48 half-hour slots per day.
    weekly_schedule: list[list[bool]]
    blocked_domains: list[str] = Field(default_factory=list, max_length=500)
    allow_domains: list[str] = Field(default_factory=list, max_length=500)
    blocked_categories: list[Literal[
        "education", "entertainment", "social", "games", "age_inappropriate", "unclassified"
    ]] = Field(default_factory=lambda: ["social", "games", "age_inappropriate"])
    blocked_apps: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @field_validator("weekly_schedule")
    @classmethod
    def validate_schedule(cls, value: list[list[bool]]) -> list[list[bool]]:
        if len(value) != 7 or any(len(day) != 48 for day in value):
            raise ValueError("weekly_schedule must contain 7 days × 48 half-hour slots")
        return value

    @field_validator("blocked_domains", "allow_domains")
    @classmethod
    def validate_domains(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            domain = value.strip().lower().rstrip(".")
            if not domain or len(domain) > 253 or "://" in domain or "/" in domain:
                raise ValueError("Use a domain name only, without URL paths or schemes")
            try:
                domain = domain.encode("idna").decode("ascii")
            except UnicodeError as exc:
                raise ValueError("Invalid domain name") from exc
            labels = domain.split(".")
            if len(labels) < 2 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
                raise ValueError("Invalid domain name")
            if domain not in normalized:
                normalized.append(domain)
        return normalized

    @field_validator("blocked_apps")
    @classmethod
    def validate_blocked_apps(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            name = value.strip().lower()
            if not name or ntpath.basename(name) != name or "/" in name or "\\" in name:
                raise ValueError("Enter an application process name only, without a file path")
            if len(name) > 260:
                raise ValueError("Application process names must be 260 characters or fewer")
            if name.endswith(".exe"):
                name = name[:-4]
            if name and name not in normalized:
                normalized.append(name)
        return normalized


class ChildRequestCreate(BaseModel):
    device_id: str = Field(min_length=1, max_length=80)
    child_id: str = Field(min_length=1, max_length=80)
    kind: Literal["extra_time", "false_block"]
    subject: str = Field(default="screen_time", min_length=1, max_length=253)


class ChildRequestReview(BaseModel):
    response: Literal["approved", "declined"]
    override_minutes: int = Field(default=0, ge=0, le=120)


EventType = Literal[
    "app_start", "app_stop", "domain_query", "blocked_app", "app_rule_match", "blocked_domain",
    "quota_warning", "locked", "unlock_request", "override_granted",
]


class EventRequest(BaseModel):
    ts: datetime
    device_id: str
    child_id: str
    type: EventType
    subject: str = Field(min_length=1, max_length=253)
    duration_sec: int = Field(default=0, ge=0)
    policy_id: str = Field(default="default-v1", max_length=80)
