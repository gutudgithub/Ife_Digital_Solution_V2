from django import forms


def add_accessible_error_attributes(form: forms.BaseForm) -> None:
    for field_name in form.errors:
        if field_name not in form.fields:
            continue
        field = form.fields[field_name]
        bound_field = form[field_name]
        field.widget.attrs["aria-invalid"] = "true"
        field.widget.attrs["aria-describedby"] = f"{bound_field.auto_id}_errors"
