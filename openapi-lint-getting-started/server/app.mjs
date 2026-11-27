import express from 'express';

const users = [
  { id: 'user-a', token: 'token-a', orgId: 'org-x', admin: false },
  { id: 'user-b', token: 'token-b', orgId: 'org-y', admin: false },
  { id: 'user-c', token: 'token-c', orgId: 'org-x', admin: false },
  { id: 'admin', token: 'token-admin', orgId: null, admin: true },
];
const usersByToken = new Map(users.map((user) => [user.token, user]));
const usersById = new Map(users.map((user) => [user.id, user]));

const orders = new Map([
  [
    '3f6c2a1e-8b4d-4c3a-9e2f-1a2b3c4d5e6f',
    {
      createdBy: 'user-a',
      body: {
        id: '3f6c2a1e-8b4d-4c3a-9e2f-1a2b3c4d5e6f',
        shopId: '7a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d',
        status: 'paid',
        amount: 1200,
        created_at: '2026-11-01T09:30:00Z',
      },
    },
  ],
]);

const skipOrderAccessCheck = process.env.SKIP_ORDER_ACCESS_CHECK === '1';

function canAccessOrder(user, order) {
  return user.admin || user.orgId === usersById.get(order.createdBy).orgId;
}

function error(res, status, code, message) {
  res.status(status).json({ code, message });
}

function authenticate(req, res, next) {
  const match = /^Bearer (.+)$/.exec(req.get('Authorization') ?? '');
  const user = match && usersByToken.get(match[1]);
  if (!user) {
    error(res, 401, 'unauthorized', 'The bearer token is missing or invalid.');
    return;
  }
  req.user = user;
  next();
}

const app = express();

app.get('/orders/:id', authenticate, (req, res) => {
  const order = orders.get(req.params.id);
  if (!order) {
    error(res, 404, 'not_found', 'The resource does not exist.');
    return;
  }
  if (!skipOrderAccessCheck && !canAccessOrder(req.user, order)) {
    error(res, 403, 'forbidden', 'The caller may not access this resource.');
    return;
  }
  res.json(order.body);
});

const port = Number(process.env.PORT ?? 3000);
app.listen(port, '127.0.0.1', (error) => {
  if (error) throw error;
  console.log(`listening on ${port}, order access check ${skipOrderAccessCheck ? 'skipped' : 'enabled'}`);
});
