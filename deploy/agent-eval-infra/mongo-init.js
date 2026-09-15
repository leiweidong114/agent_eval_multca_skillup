const appDatabase = process.env.MONGO_APP_DATABASE || 'agent_eval_metrics';
const appUser = process.env.MONGO_APP_USERNAME;
const appPassword = process.env.MONGO_APP_PASSWORD;

if (!appUser || !appPassword) {
  throw new Error('MongoDB application credentials are required');
}

db.getSiblingDB(appDatabase).createUser({
  user: appUser,
  pwd: appPassword,
  roles: [{ role: 'readWrite', db: appDatabase }],
});
