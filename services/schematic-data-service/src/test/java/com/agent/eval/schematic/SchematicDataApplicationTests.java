package com.agent.eval.schematic;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.mockito.Mockito.verify;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;

import org.bson.Document;
import org.bson.types.ObjectId;
import org.junit.jupiter.api.Test;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.query.Query;
import org.springframework.data.mongodb.core.query.Update;
import com.mongodb.client.result.UpdateResult;
import org.springframework.web.server.ResponseStatusException;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class SchematicDataApplicationTests {
    @Test
    void canonicalCollectionNameIsStable() {
        assertThat(SchematicDataController.CANONICAL_COLLECTION)
                .isEqualTo("HDschematicRationalityCollection");
    }

    @Test
    void insertStoresCanonicalDocumentAndReturnsGeneratedId() {
        MongoTemplate mongoTemplate = mock(MongoTemplate.class);
        when(mongoTemplate.insert(any(Document.class), eq(SchematicDataController.CANONICAL_COLLECTION)))
                .thenAnswer(invocation -> {
                    Document document = invocation.getArgument(0);
                    document.put("_id", new ObjectId("68cec0000000000000000001"));
                    return document;
                });
        SchematicDataController controller = new SchematicDataController(mongoTemplate);
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("uuid", "test-uuid");
        payload.put("status", "completed");
        payload.put("createUser", "100001");
        payload.put("createTime", "2026-09-20T08:00:00Z");
        payload.put("checkType", "hscope_diagram_lint");
        payload.put("checkMessage", "lint test");
        payload.put("userName", "tester");
        payload.put("hscopeProjectId", "project-1");
        payload.put("boardNum", "BOARD-1");
        payload.put("sessionId", "session-1");
        payload.put("resultText", "{\"totalSuccessRate\":90}");

        Map<String, Object> result = controller.insert(
                SchematicDataController.CANONICAL_COLLECTION, payload);

        assertThat(result.get("status")).isEqualTo("inserted");
        Map<?, ?> record = (Map<?, ?>) result.get("record");
        assertThat(record.get("_id")).isEqualTo("68cec0000000000000000001");
        assertThat(record.get("createTime")).isEqualTo(Instant.parse("2026-09-20T08:00:00Z").toString());
    }

    @Test
    void updateMetricsMatchesExistingSessionAndUuidWithoutInserting() {
        MongoTemplate mongoTemplate = mock(MongoTemplate.class);
        when(mongoTemplate.updateFirst(any(Query.class), any(Update.class), eq(Document.class),
                eq(SchematicDataController.CANONICAL_COLLECTION)))
                .thenReturn(UpdateResult.acknowledged(1, 1L, null));
        SchematicDataController controller = new SchematicDataController(mongoTemplate);
        Map<String, Object> result = controller.update(SchematicDataController.CANONICAL_COLLECTION,
                "session-1", "source-uuid", Map.of("field", "agentEvalMetrics", "value", Map.of("status", "completed")));
        assertThat(result.get("status")).isEqualTo("updated");
        assertThat(result.get("matchedCount")).isEqualTo(1L);
        verify(mongoTemplate).updateFirst(any(Query.class), any(Update.class), eq(Document.class),
                eq(SchematicDataController.CANONICAL_COLLECTION));
    }

    @Test
    void updateNeverUpsertsWhenSourceIsAbsent() {
        MongoTemplate mongoTemplate = mock(MongoTemplate.class);
        when(mongoTemplate.updateFirst(any(Query.class), any(Update.class), eq(Document.class),
                eq(SchematicDataController.CANONICAL_COLLECTION)))
                .thenReturn(UpdateResult.acknowledged(0, 0L, null));
        SchematicDataController controller = new SchematicDataController(mongoTemplate);
        assertThatThrownBy(() -> controller.update(SchematicDataController.CANONICAL_COLLECTION,
                "session-1", "missing-uuid", Map.of("field", "agentEvalMetrics", "value", Map.of("status", "completed"))))
                .isInstanceOf(ResponseStatusException.class);
    }
}
