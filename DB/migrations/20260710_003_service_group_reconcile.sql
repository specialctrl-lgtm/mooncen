-- One-time reconciliation after installing the generated service-group contract.
UPDATE courses
SET service_group = mooncen_infer_course_service_group(
    provider,
    collection_category,
    domain_category,
    source_group,
    operator_type,
    NULL,
    venue_name,
    raw_url
)
WHERE service_group IS DISTINCT FROM mooncen_infer_course_service_group(
    provider,
    collection_category,
    domain_category,
    source_group,
    operator_type,
    NULL,
    venue_name,
    raw_url
);
