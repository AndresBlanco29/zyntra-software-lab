def existing_table_columns(schema_editor, table_name):
    with schema_editor.connection.cursor() as cursor:
        return {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(cursor, table_name)
        }


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
