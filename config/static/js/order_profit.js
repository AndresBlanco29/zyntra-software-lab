(function (global) {
  function parseNumber(value) {
    const normalized = String(value || '').trim().replace(',', '.');
    if (!normalized) {
      return null;
    }
    const parsed = Number(normalized);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function formatMoney(value) {
    const numericValue = Number(value);
    if (!Number.isFinite(numericValue)) {
      return '0.00';
    }
    return numericValue.toFixed(2);
  }

  function formatPercent(value) {
    const numericValue = Number(value);
    if (!Number.isFinite(numericValue)) {
      return '—';
    }
    return numericValue.toFixed(2);
  }

  function calculateLineProfit({ cost, netUnitPrice, quantity }) {
    const parsedCost = parseNumber(cost);
    const parsedNet = parseNumber(netUnitPrice);
    const parsedQty = Math.max(parseInt(quantity || '0', 10) || 0, 0);

    if (parsedCost === null || parsedNet === null || parsedNet <= 0) {
      return {
        unitProfitAmount: null,
        profitPercent: null,
        lineProfitAmount: null,
        lineProfitPercent: null,
        lineRevenue: parsedNet !== null ? parsedNet * parsedQty : 0,
        hasCost: parsedCost !== null,
      };
    }

    const unitProfitAmount = parsedNet - parsedCost;
    const profitPercent = (1 - (parsedCost / parsedNet)) * 100;
    const lineProfitAmount = unitProfitAmount * parsedQty;
    const lineRevenue = parsedNet * parsedQty;
    const lineProfitPercent = lineRevenue > 0 ? (lineProfitAmount / lineRevenue) * 100 : null;

    return {
      unitProfitAmount,
      profitPercent,
      lineProfitAmount,
      lineProfitPercent,
      lineRevenue,
      hasCost: true,
    };
  }

  function formatProfitLabel({ amount, percent, missingCostMessage }) {
    if (amount === null || percent === null) {
      return missingCostMessage || 'Profit unavailable';
    }
    return `$${formatMoney(amount)} (${formatPercent(percent)}%)`;
  }

  global.LTGOrderProfit = {
    parseNumber,
    formatMoney,
    formatPercent,
    calculateLineProfit,
    formatProfitLabel,
  };
})(window);
