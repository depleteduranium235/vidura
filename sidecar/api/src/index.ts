import express from "express";
import { adjudicate } from "./python-bridge";

const app = express();
app.use(express.json());

const PORT = process.env.PORT || 3001;

app.get("/health", (_req, res) => {
  res.json({ status: "ok", service: "vidura-sidecar", version: "0.1.0" });
});

app.post("/adjudicate", async (req, res) => {
  try {
    const result = await adjudicate(req.body);
    res.json(result);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

app.get("/cases/:caseId", async (_req, res) => {
  res.status(501).json({ error: "Not yet implemented — reads from Z table via OData" });
});

app.listen(PORT, () => {
  console.log(`Vidura sidecar API listening on port ${PORT}`);
});
