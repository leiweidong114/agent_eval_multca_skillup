package com.agent.eval.schematic;

import java.time.Instant;
import java.util.ArrayList;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.bson.Document;
import org.bson.types.ObjectId;
import org.springframework.data.domain.Sort;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.query.Criteria;
import org.springframework.data.mongodb.core.query.Query;
import org.springframework.data.mongodb.core.query.Update;
import com.mongodb.client.result.UpdateResult;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

@RestController
@RequestMapping("/schematic/schematicData")
public class SchematicDataController {
    static final String CANONICAL_COLLECTION = "HDschematicRationalityCollection";
    private static final List<String> REQUIRED_INSERT_FIELDS = List.of(
            "uuid", "status", "createUser", "createTime", "checkType", "checkMessage",
            "userName", "hscopeProjectId", "boardNum", "sessionId", "resultText");
    private final MongoTemplate mongoTemplate;

    public SchematicDataController(MongoTemplate mongoTemplate) {
        this.mongoTemplate = mongoTemplate;
    }

    @GetMapping("/health")
    public Map<String, Object> health() {
        Document result = mongoTemplate.executeCommand("{ ping: 1 }");
        return Map.of(
                "status", "ok",
                "service", "schematic-data-service",
                "mongo", result.get("ok"));
    }

    @GetMapping("/query")
    public Map<String, Object> query(
            @RequestParam(defaultValue = CANONICAL_COLLECTION) String collectionName,
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "20") int size,
            @RequestParam(required = false) String sessionId) {
        if (!CANONICAL_COLLECTION.equals(collectionName)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "unsupported collectionName");
        }
        if (page < 1 || size < 1 || size > 100) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "page must be >= 1 and size must be 1-100");
        }

        Query query = new Query();
        if (sessionId != null && !sessionId.isBlank()) {
            query.addCriteria(Criteria.where("sessionId").is(sessionId));
        }
        long total = mongoTemplate.count(query, Document.class, collectionName);
        query.with(Sort.by(Sort.Direction.DESC, "createTime"))
                .skip((long) (page - 1) * size)
                .limit(size);
        List<Document> documents = mongoTemplate.find(query, Document.class, collectionName);
        List<Map<String, Object>> records = documents.stream()
                .map(this::normalizeDocument)
                .toList();

        Map<String, Object> response = new LinkedHashMap<>();
        response.put("records", records);
        response.put("total", total);
        response.put("page", page);
        response.put("size", size);
        response.put("collectionName", collectionName);
        response.put("resolvedCollectionName", collectionName);
        response.put("legacyCollectionFallback", false);
        response.put("sessionId", sessionId);
        return response;
    }

    @PostMapping("/insert")
    public Map<String, Object> insert(
            @RequestParam(defaultValue = CANONICAL_COLLECTION) String collectionName,
            @RequestBody Map<String, Object> payload) {
        validateCollection(collectionName);
        List<String> missing = REQUIRED_INSERT_FIELDS.stream()
                .filter(field -> payload.get(field) == null || String.valueOf(payload.get(field)).isBlank())
                .toList();
        if (!missing.isEmpty()) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    "missing required fields: " + String.join(", ", missing));
        }

        Document document = new Document();
        payload.forEach((key, value) -> {
            if (!"_id".equals(key)) {
                document.put(key, value);
            }
        });
        document.put("createTime", parseCreateTime(payload.get("createTime")));
        Document inserted = mongoTemplate.insert(document, collectionName);

        Map<String, Object> response = new LinkedHashMap<>();
        response.put("status", "inserted");
        response.put("collectionName", collectionName);
        response.put("record", normalizeDocument(inserted));
        return response;
    }

    @PutMapping("/update")
    public Map<String, Object> update(
            @RequestParam(defaultValue = CANONICAL_COLLECTION) String collectionName,
            @RequestParam String sessionId,
            @RequestParam String uuid,
            @RequestBody Map<String, Object> payload) {
        validateCollection(collectionName);
        String field = String.valueOf(payload.getOrDefault("field", ""));
        if (!List.of("agentEvalMetrics", "agentEvalProcess").contains(field)
                || !(payload.get("value") instanceof Map<?, ?>)
                || sessionId.isBlank() || uuid.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid update target or value");
        }
        Query query = new Query(Criteria.where("sessionId").is(sessionId).and("uuid").is(uuid));
        UpdateResult result = mongoTemplate.updateFirst(
                query, new Update().set(field, payload.get("value")), Document.class, collectionName);
        if (result.getMatchedCount() != 1) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "source record not found");
        }
        return Map.of("status", "updated", "collectionName", collectionName,
                "sessionId", sessionId, "uuid", uuid, "field", field,
                "matchedCount", result.getMatchedCount(), "modifiedCount", result.getModifiedCount());
    }

    private void validateCollection(String collectionName) {
        if (!CANONICAL_COLLECTION.equals(collectionName)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "unsupported collectionName");
        }
    }

    private Date parseCreateTime(Object value) {
        if (value instanceof Date date) {
            return date;
        }
        if (value instanceof Instant instant) {
            return Date.from(instant);
        }
        try {
            return Date.from(Instant.parse(String.valueOf(value)));
        } catch (RuntimeException exception) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    "createTime must be an ISO-8601 timestamp",
                    exception);
        }
    }

    private Map<String, Object> normalizeDocument(Document document) {
        Map<String, Object> result = new LinkedHashMap<>();
        document.forEach((key, value) -> result.put(key, normalizeValue(value)));
        return result;
    }

    private Object normalizeValue(Object value) {
        if (value instanceof ObjectId objectId) {
            return objectId.toHexString();
        }
        if (value instanceof Date date) {
            return date.toInstant().toString();
        }
        if (value instanceof Instant instant) {
            return instant.toString();
        }
        if (value instanceof Document document) {
            return normalizeDocument(document);
        }
        if (value instanceof Map<?, ?> map) {
            Map<String, Object> normalized = new LinkedHashMap<>();
            map.forEach((key, item) -> normalized.put(String.valueOf(key), normalizeValue(item)));
            return normalized;
        }
        if (value instanceof Iterable<?> iterable) {
            List<Object> normalized = new ArrayList<>();
            iterable.forEach(item -> normalized.add(normalizeValue(item)));
            return normalized;
        }
        return value;
    }
}

