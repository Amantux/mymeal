// myMeal Lovelace card — today's meals + quick counts.
// Add to a dashboard with:  type: custom:mymeal-card
// (auto-registered as a frontend resource by the integration).
class MyMealCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
  }

  getCardSize() {
    return 3;
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _find(suffix) {
    // Match the first sensor whose entity_id ends with the given suffix,
    // regardless of the config-entry prefix HA assigns.
    const id = Object.keys(this._hass.states).find(
      (e) => e.startsWith('sensor.') && e.includes(suffix),
    );
    return id ? this._hass.states[id] : null;
  }

  _render() {
    if (!this._hass) return;
    const today = this._find('todays_meals');
    const week = this._find('meals_planned_this_week');
    const shopping = this._find('shopping_list_items');
    const pantry = this._find('pantry_expiring');

    const meals = (today && today.attributes.meals) || [];
    const mealHtml = meals.length
      ? meals
          .map(
            (m) =>
              `<div class="meal"><span class="type">${m.mealType}</span>${m.name}</div>`,
          )
          .join('')
      : '<div class="empty">Nothing planned for today.</div>';

    const stat = (s, label) =>
      `<div class="stat"><div class="v">${s ? s.state : '–'}</div><div class="l">${label}</div></div>`;

    this.innerHTML = `
      <ha-card header="🍽️ myMeal">
        <div class="card-content">
          <div class="section-title">Today</div>
          ${mealHtml}
          <div class="stats">
            ${stat(week, 'This week')}
            ${stat(shopping, 'To buy')}
            ${stat(pantry, 'Expiring')}
          </div>
        </div>
        <style>
          .card-content { padding: 0 16px 16px; }
          .section-title { font-weight: 600; margin: 4px 0 8px; opacity: 0.7; }
          .meal { padding: 6px 0; border-bottom: 1px solid var(--divider-color); }
          .meal .type {
            display: inline-block; min-width: 76px; text-transform: capitalize;
            opacity: 0.6; font-size: 0.85em;
          }
          .empty { opacity: 0.6; padding: 6px 0; }
          .stats { display: flex; gap: 20px; margin-top: 14px; }
          .stat .v { font-size: 1.6em; font-weight: 700; }
          .stat .l { opacity: 0.6; font-size: 0.8em; }
        </style>
      </ha-card>
    `;
  }
}

customElements.define('mymeal-card', MyMealCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: 'mymeal-card',
  name: 'myMeal Card',
  description: "Today's meals and quick myMeal stats.",
});
