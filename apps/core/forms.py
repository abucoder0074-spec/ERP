from django.contrib.auth.forms import AuthenticationForm
from django.utils.translation import gettext_lazy as _


class WorkspaceAuthenticationForm(AuthenticationForm):
    error_messages = {
        "invalid_login": _("Sign-in failed. Check your credentials."),
        "inactive": _("Sign-in failed. Check your credentials."),
    }
