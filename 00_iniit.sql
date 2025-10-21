-- Create replication role
CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'replicator_password';

-- Create physical replication slot
SELECT * FROM pg_create_physical_replication_slot('replication_slot');