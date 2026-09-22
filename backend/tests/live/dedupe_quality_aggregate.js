// One-time migration for the two verified 2026-09-22 aggregate records.
// Refuse to touch a different database state; retain the newest record.
const collection = db.getSiblingDB("agent_eval_metrics")
  .getCollection("HDschematicRationalityCollection");
const scope = {sessionId: "汇总结果", checkType: "agent_eval_quality_aggregate"};
const records = collection.find(scope, {_id: 1, uuid: 1}).toArray();
if (records.length !== 2) {
  throw new Error(`Expected exactly two aggregate records, found ${records.length}`);
}
const keep = records.find(row => String(row._id) === "6ab2013c51e1945aed54ade1"
  && row.uuid === "1c69fb3b29c14b4e9c92211ae3ea37fa");
const remove = records.find(row => String(row._id) === "6ab200f351e1945aed54addb"
  && row.uuid === "05fc6312cd6841b198e8daa4821a4f49");
if (!keep || !remove) {
  throw new Error("Aggregate IDs or UUIDs changed; no deletion performed");
}
const result = collection.deleteOne({...scope, _id: remove._id, uuid: remove.uuid});
if (result.deletedCount !== 1 || collection.countDocuments(scope) !== 1) {
  throw new Error("Aggregate de-duplication did not leave exactly one record");
}
printjson({removed_id: String(remove._id), retained_id: String(keep._id), remaining: 1});
