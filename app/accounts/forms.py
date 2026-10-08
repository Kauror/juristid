"""Forms for personal sign-in and account administration.

Every password field is `strip=False` and has no `max_length` on the widget:
the validators decide length, a password is compared exactly as typed, and a
browser's password manager may fill and paste into all of them
(docs/adr/0145 §6). Labels are Estonian and short; nothing here explains the
security model to the person using it.
"""

from __future__ import annotations

from typing import Any, cast

from django import forms

from app.accounts import capabilities
from app.accounts.enums import UserRole


def _password_input(autocomplete: str, *, autofocus: bool = False) -> forms.PasswordInput:
    attrs = {
        "class": "field__input",
        "autocomplete": autocomplete,
        "autocapitalize": "off",
        "spellcheck": "false",
    }
    if autofocus:
        attrs["autofocus"] = "autofocus"
    return forms.PasswordInput(attrs=attrs)


class SignInForm(forms.Form):
    email = forms.CharField(
        label="E-post",
        max_length=320,
        widget=forms.EmailInput(
            attrs={
                "class": "field__input",
                "autocomplete": "username",
                "autocapitalize": "off",
                "spellcheck": "false",
                "autofocus": "autofocus",
            }
        ),
    )
    password = forms.CharField(
        label="Parool", strip=False, widget=_password_input("current-password")
    )


class SecondFactorForm(forms.Form):
    code = forms.CharField(
        label="Kood",
        max_length=32,
        widget=forms.TextInput(
            attrs={
                "class": "field__input",
                "autocomplete": "one-time-code",
                "inputmode": "text",
                "autocapitalize": "characters",
                "spellcheck": "false",
                "autofocus": "autofocus",
            }
        ),
        help_text="Autentimisrakenduse 6-kohaline kood või üks taastekoodidest.",
    )

    def kind(self) -> str:
        """A six-digit value is a TOTP code; anything else is tried as a recovery code."""
        code = "".join(self.cleaned_data.get("code", "").split())
        return "totp" if len(code) == 6 and all(ch in "0123456789" for ch in code) else "recovery"


class NewPasswordForm(forms.Form):
    """A new password, typed twice. The validators run in the service."""

    password = forms.CharField(
        label="Uus parool", strip=False, widget=_password_input("new-password", autofocus=True)
    )
    password_again = forms.CharField(
        label="Uus parool uuesti", strip=False, widget=_password_input("new-password")
    )

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if data.get("password") and data.get("password") != data.get("password_again"):
            self.add_error("password_again", "Paroolid ei kattu.")
        return data


class ChangePasswordForm(NewPasswordForm):
    current = forms.CharField(
        label="Praegune parool", strip=False, widget=_password_input("current-password")
    )
    field_order = ["current", "password", "password_again"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["password"].widget.attrs.pop("autofocus", None)
        self.fields["current"].widget.attrs["autofocus"] = "autofocus"


class ForgotPasswordForm(forms.Form):
    email = forms.CharField(
        label="E-post",
        max_length=320,
        widget=forms.EmailInput(
            attrs={
                "class": "field__input",
                "autocomplete": "username",
                "autocapitalize": "off",
                "spellcheck": "false",
                "autofocus": "autofocus",
            }
        ),
    )


class EnrolmentForm(forms.Form):
    code = forms.CharField(
        label="Rakenduse kood",
        max_length=12,
        widget=forms.TextInput(
            attrs={
                "class": "field__input",
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "spellcheck": "false",
            }
        ),
    )


class ReauthenticationForm(forms.Form):
    password = forms.CharField(
        label="Parool", strip=False, widget=_password_input("current-password", autofocus=True)
    )
    code = forms.CharField(
        label="Autentimisrakenduse kood",
        required=False,
        max_length=12,
        widget=forms.TextInput(
            attrs={
                "class": "field__input",
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "spellcheck": "false",
            }
        ),
    )


# -- administration --------------------------------------------------------------


def role_choices(*, include_technical: bool) -> list[tuple[str, str]]:
    roles = [
        (UserRole.SPECIALIST.value, UserRole.SPECIALIST.label),
        (UserRole.DEPARTMENT_HEAD.value, UserRole.DEPARTMENT_HEAD.label),
        (UserRole.READER.value, UserRole.READER.label),
    ]
    if include_technical:
        roles.append((UserRole.ADMINISTRATOR.value, UserRole.ADMINISTRATOR.label))
    return roles


class CreateAccountForm(forms.Form):
    display_name = forms.CharField(
        label="Nimi",
        max_length=200,
        widget=forms.TextInput(attrs={"class": "field__input", "autocomplete": "off"}),
    )
    email = forms.CharField(
        label="E-post",
        max_length=320,
        widget=forms.EmailInput(
            attrs={"class": "field__input", "autocomplete": "off", "spellcheck": "false"}
        ),
        help_text="Koja aadress. See on ka sisselogimise nimi.",
    )
    role = forms.ChoiceField(label="Roll", widget=forms.Select(attrs={"class": "field__input"}))
    external_reason = forms.CharField(
        label="Välise aadressi erandi põhjus",
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"class": "field__input", "rows": 2}),
        help_text="Ainult kui aadress ei ole Koja oma. Erandi kinnitab haldusõiguste andja.",
    )

    def __init__(self, *args: Any, include_technical: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        cast(Any, self.fields["role"]).choices = role_choices(include_technical=include_technical)


class AccountForm(forms.Form):
    """The account page's one form: name, role and permission toggles."""

    display_name = forms.CharField(
        label="Nimi",
        max_length=200,
        widget=forms.TextInput(attrs={"class": "field__input", "autocomplete": "off"}),
    )
    role = forms.ChoiceField(label="Roll", widget=forms.Select(attrs={"class": "field__input"}))

    def __init__(
        self, *args: Any, include_technical: bool = False, current_role: str = "", **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        choices = role_choices(include_technical=include_technical)
        if current_role and current_role not in {value for value, _ in choices}:
            # The account already holds a role this administrator may not hand
            # out (the technical one): it stays selectable as what it is, so
            # saving a name change does not silently demote anybody.
            choices.append((current_role, UserRole(current_role).label))
        cast(Any, self.fields["role"]).choices = choices
        for capability in capabilities.ALL_CAPABILITIES:
            self.fields[field_name(capability)] = forms.BooleanField(
                required=False,
                label=capabilities.TEXT[capability].label,
                widget=forms.CheckboxInput(attrs={"class": "checkitem__input"}),
            )

    def wanted(self) -> dict[str, bool]:
        return {
            capability: bool(self.cleaned_data.get(field_name(capability)))
            for capability in capabilities.ALL_CAPABILITIES
        }


def field_name(capability: str) -> str:
    return "cap_" + capability.replace(".", "__")


class ChangeEmailForm(forms.Form):
    email = forms.CharField(
        label="Uus e-posti aadress",
        max_length=320,
        widget=forms.EmailInput(
            attrs={"class": "field__input", "autocomplete": "off", "spellcheck": "false"}
        ),
    )
    external_reason = forms.CharField(
        label="Välise aadressi erandi põhjus",
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"class": "field__input", "rows": 2}),
    )


class DeactivateForm(forms.Form):
    reason = forms.CharField(
        label="Põhjus",
        required=False,
        max_length=500,
        widget=forms.TextInput(attrs={"class": "field__input"}),
    )
