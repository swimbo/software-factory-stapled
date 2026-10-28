// Trigger orchestrate.run from the command line.
//
//   npm run invoke -- <unitId> <target> <scenarios> [taskFile] [targetRepo]
//
// Restate SDK 1.x has no client-side connect(); invocation is a plain HTTP POST
// to the ingress's URL pattern (workflow: /<workflow>/<key>/<handler>).
// Verify against `restate openapi` if the ingress rejects this shape.

const ingress = process.env.RESTATE_INGRESS ?? "http://localhost:8080";

async function main(): Promise<void> {
  const [unitId, target, scenarios, taskFile, targetRepo] = process.argv.slice(2);
  if (!unitId || !target || !scenarios || !taskFile) {
    console.error(
      "usage: npm run invoke -- <unitId> <target> <scenarios> <taskFile> [targetRepo]",
    );
    console.error("  e.g. npm run invoke -- unit-002 http://127.0.0.1:8777 \\");
    console.error("         wall/holdout/scenarios.json $PWD/fixtures/unit-002.task.md $PWD");
    process.exit(1);
  }
  const input: Record<string, string> = { unitId, target, scenarios, taskFile };
  if (targetRepo) input.targetRepo = targetRepo;

  const resp = await fetch(`${ingress}/orchestrate/${unitId}/run`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  console.log(`submitted ${unitId}: ${resp.status}`);
  console.log(await resp.text());
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});