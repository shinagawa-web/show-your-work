jedit() {
  node -e '
    const fs = require("node:fs");
    const [file, body] = process.argv.slice(1);
    const j = JSON.parse(fs.readFileSync(file, "utf8"));
    new Function("j", body)(j);
    fs.writeFileSync(file, JSON.stringify(j, null, 2) + "\n");
  ' "$1" "$2"
}

setup_transit() { jedit turbo.json 'j.tasks.transit = { dependsOn: ["^transit"] }; j.tasks.test.dependsOn = ["transit"]'; }
setup_inputs_root() { jedit turbo.json 'j.tasks.test.inputs = ["$TURBO_DEFAULT$", "$TURBO_ROOT$/fixtures/**"]'; }
setup_passthrough() { jedit turbo.json 'j.tasks.test.passThroughEnv = ["APP_MODE"]'; }
setup_env_declared() { jedit turbo.json 'j.tasks.test.env = ["APP_MODE"]'; }

change_none() { :; }
change_b_self() { sed -i 's/, 0)/, 1)/' packages/b/src/total.ts; }
change_a_src() { sed -i 's/x + y/x - y/' packages/a/src/math.ts; }
change_root_fixture() { echo '{ "status": "broken" }' > fixtures/shared.json; }
change_root_devdep() {
  jedit package.json 'j.devDependencies.ms = "2.1.3"'
  pnpm install --no-frozen-lockfile
}
