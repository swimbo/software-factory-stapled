// Resolve a pending human-gate approval from the command line.
//
//   npm run approve -- <requestId> <approved|rejected> <approver>
//
// Restate SDK 1.x has no client-side connect(); invocation is a plain HTTP POST
// to the ingress's URL pattern (service: /<service>/<handler>).
// Verify against `restate openapi` if the ingress rejects this shape.
const ingress = process.env.RESTATE_INGRESS ?? "http://localhost:8080";

async function main(): Promise<void> {
  const [requestId, decision, approver] = process.argv.slice(2);
  if (!requestId || !decision || !approver) {
    console.error("usage: npm run approve -- <requestId> <approved|rejected> <approver>");
    process.exit(1);
  }
  if (decision !== "approved" && decision !== "rejected") {
    console.error("decision must be 'approved' or 'rejected'");
    process.exit(1);
  }

  const resp = await fetch(`${ingress}/gate/resolve`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ requestId, decision, approver }),
  });
  console.log(`resolved ${requestId}: ${resp.status}`);
  console.log(await resp.text());
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});