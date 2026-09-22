package com.agent.eval.schematic;

import java.util.Map;

import com.mongodb.client.result.DeleteResult;
import org.bson.Document;
import org.bson.types.ObjectId;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.query.Criteria;
import org.springframework.data.mongodb.core.query.Query;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

@RestController
@RequestMapping("/schematic/schematicData")
@ConditionalOnProperty(prefix = "schematic.data", name = "delete-enabled", havingValue = "true")
public class SchematicDataDeleteController {
    private static final String COLLECTION = "HDschematicRationalityCollection";
    private final MongoTemplate mongoTemplate;

    public SchematicDataDeleteController(MongoTemplate mongoTemplate) {
        this.mongoTemplate = mongoTemplate;
    }

    /** Supply exactly one selector. An _id matches one record; a sessionId matches all its records. */
    @DeleteMapping("/delete")
    public Map<String, Object> delete(
            @RequestParam(defaultValue = COLLECTION) String collectionName,
            @RequestParam(required = false) String id,
            @RequestParam(required = false) String sessionId) {
        if (!COLLECTION.equals(collectionName)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "unsupported collectionName");
        }
        boolean byId = id != null && !id.isBlank();
        boolean bySession = sessionId != null && !sessionId.isBlank();
        if (byId == bySession) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "supply exactly one of id or sessionId");
        }
        if (byId && !ObjectId.isValid(id)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "id must be a MongoDB ObjectId");
        }
        Query query = new Query(byId
                ? Criteria.where("_id").is(new ObjectId(id))
                : Criteria.where("sessionId").is(sessionId));
        DeleteResult result = mongoTemplate.remove(query, Document.class, collectionName);
        if (result.getDeletedCount() == 0) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "record not found");
        }
        return Map.of(
                "status", "deleted",
                "collectionName", collectionName,
                "selector", byId ? "id" : "sessionId",
                "value", byId ? id : sessionId,
                "deletedCount", result.getDeletedCount());
    }
}
