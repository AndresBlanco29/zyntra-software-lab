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


def add_model_fields_if_missing(apps, schema_editor, app_label, model_name, fields):
    model = apps.get_model(app_label, model_name)
    table_name = model._meta.db_table
    existing_columns = existing_table_columns(schema_editor, table_name)
    for field in fields:
        if field.column in existing_columns:
            continue
        schema_editor.add_field(model, field)


def build_field(name, field):
    field.set_attributes_from_name(name)
    return field


def separate_add_fields(app_label, model_name, field_specs):
    def add_fields_forward(apps, schema_editor):
        add_model_fields_if_missing(
            apps,
            schema_editor,
            app_label,
            model_name,
            [build_field(name, field) for name, field in field_specs],
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
