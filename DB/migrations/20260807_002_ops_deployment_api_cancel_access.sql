-- Allow the API role to terminalize queued or stale assigned deployments.
-- SELECT and INSERT are already part of the base Ops role contract.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_api')
       AND to_regclass('public.ops_deployments') IS NOT NULL THEN
        -- Converge the short-lived broad grant used during development before
        -- installing the exact two-column cancellation contract.
        REVOKE UPDATE ON TABLE ops_deployments FROM mooncen_api;
        GRANT UPDATE (deployment_status, finished_at)
            ON TABLE ops_deployments TO mooncen_api;

        IF has_table_privilege(
            'mooncen_api',
            'public.ops_deployments',
            'UPDATE'
        ) THEN
            RAISE EXCEPTION
                'mooncen_api received broad UPDATE privilege on ops_deployments';
        END IF;
        IF NOT has_column_privilege(
            'mooncen_api',
            'public.ops_deployments',
            'deployment_status',
            'UPDATE'
        ) OR NOT has_column_privilege(
            'mooncen_api',
            'public.ops_deployments',
            'finished_at',
            'UPDATE'
        ) OR has_column_privilege(
            'mooncen_api',
            'public.ops_deployments',
            'target_commit',
            'UPDATE'
        ) THEN
            RAISE EXCEPTION
                'mooncen_api deployment cancellation column privileges are invalid';
        END IF;
    END IF;
END
$$;
