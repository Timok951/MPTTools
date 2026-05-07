from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

from core.schedule_utils import is_working_day_for_user


class WorkScheduleGuardMiddleware:
    """
    Allows sign-in on off days, but blocks write operations.
    """

    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

    def __init__(self, get_response):
        self.get_response = get_response
        self.exempt_prefixes = (
            reverse("login"),
            reverse("logout"),
            reverse("password_reset_request"),
            reverse("password_reset_confirm"),
        )

    def __call__(self, request):
        if (
            request.user.is_authenticated
            and request.method not in self.SAFE_METHODS
            and not request.path.startswith(self.exempt_prefixes)
            and not is_working_day_for_user(request.user)
        ):
            messages.warning(
                request,
                "Сегодня по вашему графику нерабочий день. Изменения данных временно недоступны.",
            )
            return redirect(request.META.get("HTTP_REFERER") or reverse("about_site"))
        return self.get_response(request)
from django.db.utils import OperationalError, ProgrammingError
from django.utils import translation

from core.models import UserPreference


class UserPreferenceLocaleMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        language_code = None
        user = getattr(request, "user", None)

        if user and user.is_authenticated:
            try:
                preference = getattr(user, "preferences", None)
                if preference is None:
                    preference = UserPreference.objects.only("preferred_language").filter(user=user).first()
                if preference and preference.preferred_language:
                    language_code = preference.preferred_language
            except (ProgrammingError, OperationalError):
                language_code = None

        if language_code:
            translation.activate(language_code)
            request.LANGUAGE_CODE = language_code
            request.session["django_language"] = language_code

        response = self.get_response(request)
        if language_code:
            response.headers.setdefault("Content-Language", language_code)
        return response
