# Patch notes v14 HOTFIX

## Fixed crash loop

Previous bug:

```python
async def post_init(app):
    ...
    await scan(app)
```

New behavior:

```python
async def post_init(app):
    logger.info("Bot init complete. Auto scan disabled by default.")
```

## Fixed Button_data_invalid

Previous behavior used long match names in `callback_data`.

New behavior:

```python
sid = uuid.uuid4().hex[:10]
callback_data = f"acc:{sid}"
```

## Fixed spam

- Scan whitelist only.
- Max candidates sent by env.
- Send errors do not crash app.
- Daily scan disabled by default.

## Fixed in-memory-only bets

Open bets/results/bank are persisted to JSON:

```text
/app/data/state.json
```
