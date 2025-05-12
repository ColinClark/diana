# in the project directory
docker compose exec -T broker  \
  kafka-topics --bootstrap-server localhost:9092 --create \
    --replication-factor 1 --partitions 1 --topic web-events2 2>/dev/null || true

cat events.ndjson | docker compose exec -T broker  \
  kafka-console-producer --bootstrap-server localhost:9092 --topic web-events2 --property "parse.key=false"

