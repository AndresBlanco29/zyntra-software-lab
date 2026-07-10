import importlib

from django.db import migrations


def existing_table_columns(schema_editor, table_name):
    with schema_editor.connection.cursor() as cursor:
        return {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(cursor, table_name)
        }


def table_exists(schema_editor, table_name):
    with schema_editor.connection.cursor() as cursor:
        return table_name in schema_editor.connection.introspection.table_names(cursor)


def bind_field_remote_model(field, apps):
    remote_field = getattr(field, 'remote_field', None)
    if remote_field is None:
        return field
    remote_model = remote_field.model
    if isinstance(remote_model, str) and '.' in remote_model:
        related_app_label, related_model_name = remote_model.split('.', 1)
        remote_field.model = apps.get_model(related_app_label, related_model_name)
    # Historical FK rebuilds often leave field_name=None; schema_editor.add_field
    # then calls get_field(None) and crashes.
    if getattr(remote_field, 'field_name', None) is None and remote_field.model is not None:
        if not isinstance(remote_field.model, str):
            remote_field.field_name = remote_field.model._meta.pk.name
    return field


def rebuild_field(name, field, apps):
    deconstructed = field.deconstruct()
    # Django historically returned (path, args, kwargs); newer versions may
    # include the field name as a leading element: (name, path, args, kwargs).
    if len(deconstructed) == 4:
        _field_name, path, args, kwargs = deconstructed
    else:
        path, args, kwargs = deconstructed
    module_path, class_name = path.rsplit('.', 1)
    field_class = getattr(importlib.import_module(module_path), class_name)
    rebuilt = field_class(*args, **kwargs)
    rebuilt.set_attributes_from_name(name)
    return bind_field_remote_model(rebuilt, apps)


def add_model_fields_if_missing(apps, schema_editor, app_label, model_name, fields):
    model = apps.get_model(app_label, model_name)
    table_name = model._meta.db_table
    existing_columns = existing_table_columns(schema_editor, table_name)
    for field in fields:
        if field.column in existing_columns:
            continue
        schema_editor.add_field(model, field)


def build_field(name, field, apps):
    return rebuild_field(name, field, apps)


def separate_add_fields(app_label, model_name, field_specs):
    def add_fields_forward(apps, schema_editor):
        add_model_fields_if_missing(
            apps,
            schema_editor,
            app_label,
            model_name,
            [build_field(name, field, apps) for name, field in field_specs],
        )

    return migrations.SeparateDatabaseAndState(
        database_operations=[
            migrations.RunPython(add_fields_forward, migrations.RunPython.noop),
        ],
        state_operations=[
            migrations.AddField(model_name=model_name, name=name, field=field)
            for name, field in field_specs
        ],
    )


def wrap_add_field_operations(app_label, operations):
    wrapped = []
    pending_by_model = {}

    def flush():
        nonlocal pending_by_model
        for model_name, field_specs in pending_by_model.items():
            wrapped.append(separate_add_fields(app_label, model_name, field_specs))
        pending_by_model = {}

    for operation in operations:
        if isinstance(operation, migrations.AddField):
            pending_by_model.setdefault(operation.model_name, []).append(
                (operation.name, operation.field),
            )
        else:
            flush()
            wrapped.append(operation)
    flush()
    return wrapped


def create_model_if_missing(model_class, schema_editor):
    table_name = model_class._meta.db_table
    if table_exists(schema_editor, table_name):
        return
    schema_editor.create_model(model_class)


def separate_create_model(model_class, create_model_operation):
    def forward(apps, schema_editor):
        create_model_if_missing(model_class, schema_editor)

    return migrations.SeparateDatabaseAndState(
        database_operations=[
            migrations.RunPython(forward, migrations.RunPython.noop),
        ],
        state_operations=[create_model_operation],
    )
