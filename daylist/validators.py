import re

from django.core.exceptions import ValidationError


class NumberPasswordValidator:
    def validate(self, password, user=None):
        if not re.search(r"[0-9]", password):
            raise ValidationError(self.get_help_text(), code="password_no_number")

    def get_help_text(self):
        return "Your password must contain at least one number."
