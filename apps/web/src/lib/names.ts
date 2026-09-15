/** Blueprint names follow the schema's id rule (packages/blueprint_schema, `_ID`). */
export const BLUEPRINT_NAME = /^[a-z][a-z0-9-]{1,62}$/;

/** A valid blueprint name to start from when importing an instance (instance ids may hold dots or lead with a digit). */
export function suggestBlueprintName(instanceId: string): string {
  let name = `${instanceId.toLowerCase().replace(/[^a-z0-9-]+/g, "-")}-imported`.replace(/-+/g, "-").replace(/^-/, "");
  if (!/^[a-z]/.test(name)) name = `fleet-${name}`;
  return name.slice(0, 63).replace(/-+$/, "");
}
