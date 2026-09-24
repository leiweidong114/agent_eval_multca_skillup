package com.agent.eval.schematic;

import java.util.Map;

import com.mongodb.client.result.DeleteResult;
import org.bson.Document;
import org.bson.types.ObjectId;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.query.Criteria;
import org.springframework.data.mongodb.core.query.Query;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

@RestController
@RequestMapping("/schematic/schematicData")
public class SchematicDataDeleteController {
    private static final String COLLECTION = "HDschematicRationalityCollection";
    private final MongoTemplate mongoTemplate;

    public SchematicDataDeleteController(MongoTemplate mongoTemplate) {
        this.mongoTemplate = mongoTemplate;
    }

    /** Supply collectionName and exactly one of _id, sessionId or uuid in the JSON body. */
    @PostMapping("/delete")
    public Map<String, Object> delete(@RequestBody Map<String, Object> payload) {
        String collectionName = value(payload, "collectionName");
        if (!COLLECTION.equals(collectionName)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "unsupported collectionName");
        }
        String id = value(payload, "_id");
        String sessionId = value(payload, "sessionId");
        String uuid = value(payload, "uuid");
        int selectors = (id == null ? 0 : 1) + (sessionId == null ? 0 : 1) + (uuid == null ? 0 : 1);
        if (selectors != 1) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "supply exactly one of _id, sessionId or uuid");
        }
        if (id != null && !ObjectId.isValid(id)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "_id must be a MongoDB ObjectId");
        }
        String selector = id != null ? "_id" : sessionId != null ? "sessionId" : "uuid";
        String selectorValue = id != null ? id : sessionId != null ? sessionId : uuid;
        Query query = new Query(id != null
                ? Criteria.where("_id").is(new ObjectId(id))
                : Criteria.where(selector).is(selectorValue));
        DeleteResult result = mongoTemplate.remove(query, Document.class, collectionName);
        if (result.getDeletedCount() == 0) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "record not found");
        }
        return Map.of(
                "status", "deleted",
                "collectionName", collectionName,
                "selector", selector,
                "value", selectorValue,
                "deletedCount", result.getDeletedCount());
    }

    private String value(Map<String, Object> payload, String key) {
        Object raw = payload.get(key);
        if (raw == null || String.valueOf(raw).isBlank()) {
            return null;
        }
        return String.valueOf(raw).trim();
    }
}
