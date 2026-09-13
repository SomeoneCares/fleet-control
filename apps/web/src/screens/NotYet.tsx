import { useParams } from "react-router";
import { ALL_NAV_ITEMS } from "../components/Shell";
import { Card, Icon, PageHeader } from "../components/ui";

export function NotYetScreen() {
  const { key = "" } = useParams();
  const item = ALL_NAV_ITEMS.find((i) => i.key === key);
  const label = item?.label ?? (key === "settings" ? "Settings" : "This screen");
  const slice = item?.slice ?? 1;
  return (
    <>
      <PageHeader crumb="Not built yet" title={label} />
      <Card className="p-8 max-w-[640px]">
        <div className="flex gap-3 items-start">
          <Icon name="clock" size={20} className="text-text-secondary mt-0.5" />
          <div>
            <p className="m-0 font-semibold">Planned for Slice {slice} of the build plan.</p>
            <p className="mt-1 mb-0 text-text-secondary">
              The design is in <code className="font-mono text-[12px]">design/</code>; the screen is built when its slice starts
              (docs/build-document.md, section 9). Instances, Blueprints, plans and drift work today.
            </p>
          </div>
        </div>
      </Card>
    </>
  );
}
