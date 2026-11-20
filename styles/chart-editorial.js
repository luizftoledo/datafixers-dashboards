(() => {
  if (!window.Chart?.defaults) return;
  const { defaults } = window.Chart;
  defaults.color = '#595764';
  defaults.borderColor = '#e2dfd9';
  defaults.font.family = 'Inter, system-ui, sans-serif';
  defaults.font.size = 12;
  defaults.plugins.legend.labels.usePointStyle = true;
  defaults.plugins.legend.labels.boxWidth = 9;
  defaults.plugins.legend.labels.boxHeight = 9;
  defaults.plugins.legend.labels.padding = 14;
  defaults.plugins.tooltip.backgroundColor = '#302742';
  defaults.plugins.tooltip.padding = 10;
  defaults.elements.line.borderWidth = 2.5;
  defaults.elements.point.hoverRadius = 5;
  defaults.scale.grid.color = '#e2dfd9';
})();
