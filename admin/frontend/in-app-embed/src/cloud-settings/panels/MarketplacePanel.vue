<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import SettingsHeader from "frappe-ui/src/components/SettingsDialog/SettingsHeader.vue";
import SettingsBody from "frappe-ui/src/components/SettingsDialog/SettingsBody.vue";
import Button from "frappe-ui/src/components/Button/Button.vue";
import FormControl from "frappe-ui/src/components/FormControl/FormControl.vue";
import Select from "frappe-ui/src/components/Select/Select.vue";
import PanelState from "../components/PanelState.vue";
import AppRow from "../components/AppRow.vue";
import UpdateAppsDialog from "../components/UpdateAppsDialog.vue";
import ActionableError from "../components/ActionableError.vue";
import ErrorMessage from "frappe-ui/src/components/ErrorMessage/ErrorMessage.vue";
import { waitForTask } from "../store";

const props = defineProps({
  store: { type: Object, required: true },
  active: { type: Boolean, default: false },
});
const store = props.store;

// Wording per action, so every toast names both the action and the app.
const ACTION = {
  install: {
    progress: __("Installing"),
    done: __("installed"),
    verb: __("install"),
  },
  uninstall: {
    progress: __("Uninstalling"),
    done: __("uninstalled"),
    verb: __("uninstall"),
  },
  update: { progress: __("Updating"), done: __("updated"), verb: __("update") },
};

const query = ref("");
const category = ref("");
const pending = reactive({});
const errors = reactive({});
const showUpdates = ref(false);
const updatingAll = ref(false);
const updateAllError = ref("");
// Bench-wide, not per-app: shown once above the list.
const blocker = ref(null);

// Guards the task poller: a dialog closed mid-task must not keep polling.
let gone = false;
onBeforeUnmount(() => (gone = true));
watch(
  () => props.active,
  (active) => {
    if (active) store.loadMarketplace();
  },
  { immediate: true },
);

const marketplace = computed(() => store.state.marketplace);
const error = computed(() => store.state.marketplaceError);
// Only a first load replaces the list; a failed refresh keeps it and reports inline.
const loadFailed = computed(() => Boolean(error.value) && !marketplace.value);
const updateCount = computed(() => marketplace.value?.update_count || 0);
const appsWithUpdates = computed(() =>
  (marketplace.value?.apps || []).filter((app) => app.has_update),
);

const categoryOptions = computed(() => [
  { label: __("All categories"), value: "" },
  ...(marketplace.value?.categories || []).map((c) =>
    typeof c === "string" ? { label: c, value: c } : c,
  ),
]);

const filteredApps = computed(() => {
  const term = query.value.trim().toLowerCase();
  return (marketplace.value?.apps || []).filter((app) => {
    if (category.value && app.category !== category.value) return false;
    if (!term) return true;
    return `${app.title} ${app.description}`.toLowerCase().includes(term);
  });
});

function clearFilters() {
  query.value = "";
  category.value = "";
}

const install = (app) =>
  runAction(app, "install", () => store.api.installApp(app.name));
const uninstall = (app) =>
  runAction(app, "uninstall", () => store.api.uninstallApp(app.name));
const updateOne = (app) =>
  runAction(app, "update", () => store.api.updateApps([app.name]));

// An unresolved update operation blocks every update: link to the page that clears it
// rather than invite a retry that cannot succeed.
function asBlocker(exception) {
  if (!store.api.isMigrationConflict(exception)) return null;
  const server = store.state.context.server_url;
  return {
    message: store.api.getErrorMessage(exception),
    actionLabel: server ? __("Open updates") : "",
    actionUrl: server ? `${server.replace(/\/$/, "")}/updates` : "",
  };
}

async function updateAll({ apps }) {
  updatingAll.value = true;
  updateAllError.value = "";
  blocker.value = null;
  try {
    const { task_id } = await store.api.updateApps(apps);
    await settle(task_id, ACTION.update, __("all apps"));
    await store.loadMarketplace(true);
    showUpdates.value = false;
  } catch (exception) {
    blocker.value = asBlocker(exception);
    if (blocker.value) {
      showUpdates.value = false;
    } else {
      updateAllError.value = store.api.getErrorMessage(exception);
      // The dialog may already be closed, where its inline error is invisible.
      if (!showUpdates.value) notify(updateAllError.value, "red");
    }
  } finally {
    updatingAll.value = false;
  }
}

// Track the bench task to completion, keeping the row pending throughout.
async function runAction(app, verb, action) {
  errors[app.name] = "";
  blocker.value = null;
  pending[app.name] = verb;
  try {
    const { task_id } = await action();
    await settle(task_id, ACTION[verb], app.title);
    delete errors[app.name];
    await store.loadMarketplace(true);
  } catch (exception) {
    blocker.value = asBlocker(exception);
    if (!blocker.value) {
      errors[app.name] = store.api.getErrorMessage(exception);
      notify(errors[app.name], "red");
    }
  } finally {
    delete pending[app.name];
  }
}

// Throws on failure so the caller records a row error; a lost task is only a
// warning, because it may still be running.
async function settle(taskId, action, label) {
  if (!taskId) return;
  const outcome = await waitForTask(taskId, () => gone);
  if (outcome === "cancelled") return;
  if (outcome === "success") {
    notify(__("{0} {1}.", [label, action.done]), "green");
  } else if (outcome === "failed" || outcome === "error") {
    throw new Error(__("Couldn't {0} {1}.", [action.verb, label]));
  } else {
    notify(
      __(
        "{0} {1} is taking longer than expected. It will keep running in the background — reopen to check.",
        [action.progress, label],
      ),
      "orange",
    );
  }
}

function notify(message, indicator = "green") {
  frappe.show_alert({ message, indicator });
}
</script>

<template>
  <!-- Filters sit in the pinned header so only the list scrolls; that means
       replacing the header's default layout. -->
  <SettingsHeader>
    <div class="flex items-start justify-between gap-4">
      <div class="flex min-w-0 flex-col gap-1">
        <h2 class="text-lg font-semibold text-ink-gray-8">
          {{ __("Marketplace") }}
        </h2>
        <p class="text-base text-ink-gray-6">
          {{ __("Install apps and keep them up to date.") }}
        </p>
      </div>
      <Button
        v-if="updateCount"
        variant="solid"
        class="shrink-0"
        :loading="updatingAll"
        @click="((updateAllError = ''), (showUpdates = true))"
      >
        {{
          updatingAll ? __("Updating") : __("Update all ({0})", [updateCount])
        }}
      </Button>
    </div>

    <div v-if="marketplace" class="mt-8 flex items-center gap-2">
      <FormControl
        v-model="query"
        type="text"
        class="flex-1"
        :placeholder="__('Search apps')"
      />
      <Select
        v-model="category"
        :options="categoryOptions"
        class="w-[184px] shrink-0"
      />
    </div>
  </SettingsHeader>

  <SettingsBody>
    <PanelState
      class="pt-6"
      :loading="!marketplace && !error"
      :error="loadFailed ? error : ''"
      :title="__(`Couldn't load the marketplace`)"
      @retry="store.loadMarketplace(true)"
    >
      <ErrorMessage :message="loadFailed ? '' : error" class="mb-4" />

      <ActionableError
        v-if="blocker"
        class="mb-4"
        :message="blocker.message"
        :action-label="blocker.actionLabel"
        :action-url="blocker.actionUrl"
      />

      <div
        v-if="!filteredApps.length"
        class="flex flex-col items-center gap-2 py-10 text-center"
      >
        <p class="text-base text-ink-gray-6">
          {{ __("No apps match your search.") }}
        </p>
        <Button @click="clearFilters">{{ __("Clear filters") }}</Button>
      </div>

      <div v-else class="grid grid-cols-2 gap-x-8">
        <AppRow
          v-for="app in filteredApps"
          :key="app.name"
          :app="app"
          :pending="pending[app.name] || ''"
          :error="errors[app.name] || ''"
          @install="install"
          @uninstall="uninstall"
          @update="updateOne"
        />
      </div>
    </PanelState>
  </SettingsBody>

  <UpdateAppsDialog
    v-model="showUpdates"
    :apps="appsWithUpdates"
    :updating="updatingAll"
    :error="updateAllError"
    @submit="updateAll"
  />
</template>
