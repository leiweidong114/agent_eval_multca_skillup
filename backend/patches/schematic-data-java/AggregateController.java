package com.agent.eval.schematic;

import java.time.Instant;
import java.util.Date;
import java.util.Map;
import java.util.UUID;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.bson.Document;
import org.springframework.data.domain.Sort;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.FindAndModifyOptions;
import org.springframework.data.mongodb.core.index.Index;
import org.springframework.data.mongodb.core.index.PartialIndexFilter;
import org.springframework.data.mongodb.core.query.Criteria;
import org.springframework.data.mongodb.core.query.Query;
import org.springframework.data.mongodb.core.query.Update;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/** One atomic, narrowly scoped write path for the cross-session rollup. */
@RestController
@RequestMapping("/schematic/schematicData")
public class AggregateController {
    static final String COLLECTION = "HDschematicRationalityCollection";
    static final String SESSION_ID = "汇总结果";
    static final String CHECK_TYPE = "agent_eval_quality_aggregate";
    private final MongoTemplate mongoTemplate;
    private final ObjectMapper objectMapper;

    public AggregateController(MongoTemplate mongoTemplate, ObjectMapper objectMapper) {
        this.mongoTemplate = mongoTemplate;
        this.objectMapper = objectMapper;
    }

    @PutMapping("/aggregate")
    public Map<String, Object> upsert(
            @RequestParam(defaultValue = COLLECTION) String collectionName,
            @RequestBody Map<String, Object> payload) {
        if (!COLLECTION.equals(collectionName)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "unsupported collectionName");
        }
        if (!(payload.get("rates") instanceof Map<?, ?>)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "rates object is required");
        }
        Date updatedAt;
        try {
            updatedAt = Date.from(Instant.parse(String.valueOf(payload.get("updated_at"))));
        } catch (RuntimeException exception) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "updated_at must be ISO-8601", exception);
        }
        String resultText;
        try {
            resultText = objectMapper.writeValueAsString(payload);
        } catch (JsonProcessingException exception) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid aggregate JSON", exception);
        }

        // This partial index affects only the named aggregate, not source reports.
        mongoTemplate.indexOps(collectionName).ensureIndex(
                new Index().on("sessionId", Sort.Direction.ASC)
                        .on("checkType", Sort.Direction.ASC)
                        .unique()
                        .partial(PartialIndexFilter.of(
                                Criteria.where("sessionId").is(SESSION_ID).and("checkType").is(CHECK_TYPE))));
        Query query = new Query(Criteria.where("sessionId").is(SESSION_ID)
                .and("checkType").is(CHECK_TYPE));
        Update update = new Update()
                .set("agentEvalMetrics", payload)
                .set("resultText", resultText)
                .set("status", "completed")
                .set("createTime", updatedAt)
                .setOnInsert("uuid", UUID.randomUUID().toString().replace("-", ""))
                .setOnInsert("createUser", "agent-eval")
                .setOnInsert("checkMessage", "Agent Eval 全部已计算会话累计质量指标")
                .setOnInsert("userName", "Agent Eval")
                .setOnInsert("hscopeProjectId", "quality-aggregate")
                .setOnInsert("boardNum", "aggregate-v1");
        Document saved = mongoTemplate.findAndModify(query, update,
                FindAndModifyOptions.options().upsert(true).returnNew(true),
                Document.class, collectionName);
        if (saved == null) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "aggregate upsert returned no record");
        }
        return Map.of("status", "updated", "collectionName", collectionName,
                "sessionId", SESSION_ID, "checkType", CHECK_TYPE,
                "matchedCount", 1, "recordId", String.valueOf(saved.get("_id")));
    }
}
