import { execFile } from "child_process";
import path from "path";

const PYTHON_CORE_DIR = path.resolve(__dirname, "../../core");

interface AdjudicateInput {
  case_id: string;
  bp_id: string;
  bp_name: string;
  spl_entry_id: string;
  spl_entry_name: string;
  [key: string]: unknown;
}

interface AdjudicateResult {
  case_id: string;
  disposition_band: string;
  rationale: string;
  evidence_summary: string;
  [key: string]: unknown;
}

export function adjudicate(input: AdjudicateInput): Promise<AdjudicateResult> {
  return new Promise((resolve, reject) => {
    const scriptPath = path.join(PYTHON_CORE_DIR, "run_adjudication.py");
    const proc = execFile(
      "python",
      [scriptPath, JSON.stringify(input)],
      { cwd: PYTHON_CORE_DIR, timeout: 60000 },
      (error, stdout, stderr) => {
        if (error) {
          reject(new Error(`Python bridge error: ${stderr || error.message}`));
          return;
        }
        try {
          resolve(JSON.parse(stdout));
        } catch {
          reject(new Error(`Failed to parse Python output: ${stdout}`));
        }
      }
    );
  });
}
