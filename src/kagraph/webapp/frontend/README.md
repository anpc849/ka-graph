# KaGraph Studio Frontend

Next.js frontend for KaGraph Studio. It provides a dense trace workspace for graph execution, live event streams, messages, state updates, checkpoints, and replay controls.

## Development

```bash
npm install
npm run dev
```

The frontend expects the Studio backend at `http://127.0.0.1:8000` unless `NEXT_PUBLIC_KATRACE_API_URL` is set.

## Production check

```bash
npm run build
```
