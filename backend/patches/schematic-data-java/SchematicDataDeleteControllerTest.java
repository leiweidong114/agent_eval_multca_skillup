package com.agent.eval.schematic;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.util.Map;

import com.mongodb.client.result.DeleteResult;
import org.bson.Document;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.query.Query;
import org.springframework.web.server.ResponseStatusException;

class SchematicDataDeleteControllerTest {
    private static final String COLLECTION = "HDschematicRationalityCollection";

    @Test
    void idDeletesOnlyTheMatchingObjectId() {
        MongoTemplate mongo = mock(MongoTemplate.class);
        when(mongo.remove(any(Query.class), eq(Document.class), eq(COLLECTION)))
                .thenReturn(DeleteResult.acknowledged(1));
        var controller = new SchematicDataDeleteController(mongo);

        Map<String, Object> response = controller.delete(COLLECTION, "68cec0000000000000000001", null);

        assertThat(response.get("deletedCount")).isEqualTo(1L);
        assertThat(response.get("selector")).isEqualTo("id");
        ArgumentCaptor<Query> query = ArgumentCaptor.forClass(Query.class);
        verify(mongo).remove(query.capture(), eq(Document.class), eq(COLLECTION));
        assertThat(query.getValue().getQueryObject().get("_id").toString())
                .isEqualTo("68cec0000000000000000001");
    }

    @Test
    void sessionIdDeletesAllMatchingRecords() {
        MongoTemplate mongo = mock(MongoTemplate.class);
        when(mongo.remove(any(Query.class), eq(Document.class), eq(COLLECTION)))
                .thenReturn(DeleteResult.acknowledged(4));
        var controller = new SchematicDataDeleteController(mongo);

        Map<String, Object> response = controller.delete(COLLECTION, null, "session-1");

        assertThat(response.get("deletedCount")).isEqualTo(4L);
        ArgumentCaptor<Query> query = ArgumentCaptor.forClass(Query.class);
        verify(mongo).remove(query.capture(), eq(Document.class), eq(COLLECTION));
        assertThat(query.getValue().getQueryObject().getString("sessionId")).isEqualTo("session-1");
    }

    @Test
    void rejectsMissingAmbiguousOrInvalidSelectors() {
        var controller = new SchematicDataDeleteController(mock(MongoTemplate.class));
        assertThatThrownBy(() -> controller.delete(COLLECTION, null, null))
                .isInstanceOf(ResponseStatusException.class);
        assertThatThrownBy(() -> controller.delete(COLLECTION, "68cec0000000000000000001", "session-1"))
                .isInstanceOf(ResponseStatusException.class);
        assertThatThrownBy(() -> controller.delete(COLLECTION, "not-an-object-id", null))
                .isInstanceOf(ResponseStatusException.class);
        assertThatThrownBy(() -> controller.delete("other", null, "session-1"))
                .isInstanceOf(ResponseStatusException.class);
    }

    @Test
    void returnsNotFoundWithoutDeletingAnythingElse() {
        MongoTemplate mongo = mock(MongoTemplate.class);
        when(mongo.remove(any(Query.class), eq(Document.class), eq(COLLECTION)))
                .thenReturn(DeleteResult.acknowledged(0));
        var controller = new SchematicDataDeleteController(mongo);
        assertThatThrownBy(() -> controller.delete(COLLECTION, null, "missing-session"))
                .isInstanceOf(ResponseStatusException.class)
                .hasMessageContaining("404");
    }
}
