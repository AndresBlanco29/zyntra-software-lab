WORKFLOW_DRAFTS_SESSION_KEY = 'backoffice_workflow_form_drafts'

INVOICE_PICKUP_DRAFT_SCOPE = 'invoice_pickup'
INVOICE_ADJUSTMENT_DRAFT_SCOPE = 'invoice_adjustment'
DELIVERY_COMPLETE_DRAFT_SCOPE = 'delivery_complete'
DELIVERY_NOTE_DRAFT_SCOPE = 'delivery_note'

_POST_DRAFT_EXCLUDED_KEYS = frozenset({'csrfmiddlewaretoken'})


def serialize_post_data(post_data, *, exclude=None):
	excluded = _POST_DRAFT_EXCLUDED_KEYS.union(exclude or ())
	return {
		key: post_data.get(key)
		for key in post_data
		if key not in excluded
	}


def get_workflow_draft(session, scope, entity_id):
	bucket = session.get(WORKFLOW_DRAFTS_SESSION_KEY, {})
	return dict(bucket.get(scope, {}).get(str(entity_id), {}))


def set_workflow_draft(session, scope, entity_id, draft):
	bucket = dict(session.get(WORKFLOW_DRAFTS_SESSION_KEY, {}))
	scope_bucket = dict(bucket.get(scope, {}))
	entity_key = str(entity_id)
	if draft:
		scope_bucket[entity_key] = draft
	else:
		scope_bucket.pop(entity_key, None)
	bucket[scope] = scope_bucket
	session[WORKFLOW_DRAFTS_SESSION_KEY] = bucket
	session.modified = True


def merge_post_into_workflow_draft(session, scope, entity_id, post_data, *, exclude=None):
	draft = get_workflow_draft(session, scope, entity_id)
	draft.update(serialize_post_data(post_data, exclude=exclude))
	set_workflow_draft(session, scope, entity_id, draft)


def clear_workflow_draft(session, scope, entity_id):
	set_workflow_draft(session, scope, entity_id, None)


def remove_post_prefix_from_workflow_draft(session, scope, entity_id, prefix):
	draft = get_workflow_draft(session, scope, entity_id)
	if not draft:
		return
	filtered = {
		key: value
		for key, value in draft.items()
		if not key.startswith(prefix)
	}
	set_workflow_draft(session, scope, entity_id, filtered)


def clear_invoice_workflow_drafts(session, invoice_id):
	clear_workflow_draft(session, INVOICE_PICKUP_DRAFT_SCOPE, invoice_id)
	clear_workflow_draft(session, INVOICE_ADJUSTMENT_DRAFT_SCOPE, invoice_id)
