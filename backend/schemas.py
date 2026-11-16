from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name is required")
        return v

    @field_validator("password")
    @classmethod
    def password_bytes(cls, v: str) -> str:
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 bytes")
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: str
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class CheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    website_id: int
    requested_url: str
    final_url: str | None
    status_code: int | None
    response_time_ms: float | None
    is_reachable: bool
    health_status: str
    page_title: str | None
    content_type: str | None
    server: str | None
    redirect_count: int
    error_message: str | None
    checked_at: datetime


class WebsiteIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    display_name: str | None = Field(default=None, max_length=100)


class WebsiteUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)
    monitoring_enabled: bool | None = None


class WebsiteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    url: str
    display_name: str | None
    monitoring_enabled: bool
    created_at: datetime
    latest: CheckOut | None = None


class DemoIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)