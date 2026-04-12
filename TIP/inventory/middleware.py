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
