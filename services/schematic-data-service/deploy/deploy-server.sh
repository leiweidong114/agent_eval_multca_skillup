#!/usr/bin/env bash
set -euo pipefail

base_dir=/opt/schematic-data-java
mongo_container=agent-eval-mongodb
app_container=schematic-data-java
runtime_image=docker.m.daocloud.io/library/eclipse-temurin:21-jre

mongo_env="$(docker inspect "$mongo_container" --format '{{range .Config.Env}}{{println .}}{{end}}')"
mongo_user="$(printf '%s\n' "$mongo_env" | sed -n 's/^MONGO_INITDB_ROOT_USERNAME=//p' | head -n 1)"
mongo_password="$(printf '%s\n' "$mongo_env" | sed -n 's/^MONGO_INITDB_ROOT_PASSWORD=//p' | head -n 1)"
test -n "$mongo_user"
test -n "$mongo_password"

install -d -m 0755 "$base_dir/app"
install -m 0644 "$base_dir/src/target/schematic-data-service-1.0.0.jar" "$base_dir/app/app.jar"
umask 077
{
    printf 'MONGODB_HOST=127.0.0.1\n'
    printf 'MONGODB_PORT=27017\n'
    printf 'MONGODB_DATABASE=agent_eval_metrics\n'
    printf 'MONGODB_AUTH_DATABASE=admin\n'
    printf 'MONGODB_USERNAME=%s\n' "$mongo_user"
    printf 'MONGODB_PASSWORD=%s\n' "$mongo_password"
    printf 'SERVER_ADDRESS=127.0.0.1\n'
    printf 'SERVER_PORT=18081\n'
} > "$base_dir/service.env"

docker pull "$runtime_image"
docker rm -f "$app_container" >/dev/null 2>&1 || true
docker run -d \
    --name "$app_container" \
    --restart unless-stopped \
    --network host \
    --env-file "$base_dir/service.env" \
    -v "$base_dir/app/app.jar:/app/app.jar:ro" \
    "$runtime_image" \
    java -jar /app/app.jar

for attempt in $(seq 1 30); do
    if curl --noproxy '*' --fail --silent \
        http://127.0.0.1:18081/schematic/schematicData/health >/dev/null; then
        echo 'schematic-data-java is healthy'
        exit 0
    fi
    sleep 2
done

docker logs --tail 100 "$app_container"
exit 1
