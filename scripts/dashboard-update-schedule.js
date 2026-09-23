(() => {
  const schedules = {
    lai: { label: "mensal", maxAgeDays: 40, workflow: "update-lai-dashboard.yml" },
    basometro: { label: "semanal", maxAgeDays: 10, workflow: "basometro.yml" },
  };

  function formatDateTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "data indisponível";
    return new Intl.DateTimeFormat("pt-BR", {
      dateStyle: "short",
      timeStyle: "short",
      timeZone: "America/Sao_Paulo",
    }).format(date);
  }

  function buildNotice(name, updatedAt) {
    const schedule = schedules[name];
    if (!schedule) return null;
    const date = new Date(updatedAt);
    const valid = !Number.isNaN(date.getTime());
    const stale = !valid || Date.now() - date.getTime() > schedule.maxAgeDays * 86400000;
    return {
      stale,
      text: valid
        ? `Atualização ${schedule.label}. Últimos dados: ${formatDateTime(updatedAt)}${stale ? " — atualização atrasada" : ""}.`
        : `Atualização ${schedule.label}. Data dos dados indisponível.`,
    };
  }

  function applyHealthState(name, updatedAt, element) {
    const schedule = schedules[name];
    if (!schedule || !element) return;
    const notice = buildNotice(name, updatedAt);
    element.classList.remove("is-loading", "is-ok", "is-fail");
    element.classList.add(notice.stale ? "is-fail" : "is-ok");
    element.textContent = notice.stale ? "Dados atrasados · ver rotina" : "Ver rotina de atualização";
    element.href = `https://github.com/luizftoledo/datafixers-dashboards/actions/workflows/${schedule.workflow}`;
    element.title = "Indicador baseado na data dos dados; consulte o histórico da rotina no GitHub.";
  }

  window.DashboardUpdateSchedule = { formatDateTime, buildNotice, applyHealthState };
})();
