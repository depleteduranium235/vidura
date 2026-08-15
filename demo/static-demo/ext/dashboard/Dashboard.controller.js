sap.ui.define([
  "sap/ui/core/mvc/Controller",
  "sap/ui/model/json/JSONModel",
  "sap/ui/model/Filter",
  "sap/ui/model/FilterOperator"
], function (Controller, JSONModel, Filter, FilterOperator) {
  "use strict";

  return Controller.extend("zpwc.gts.spladjudication.ext.dashboard.Dashboard", {

    onInit: function () {
      this._oDashboardModel = new JSONModel();
      this.getView().setModel(this._oDashboardModel, "dashboard");
      this._loadData();
    },

    _loadData: function () {
      var that = this;

      // In an FPM custom page, the OData model may not be on the component yet
      // at onInit time. Try multiple resolution paths.
      var oModel = this.getView().getModel()
        || (this.getOwnerComponent() && this.getOwnerComponent().getModel());

      if (oModel && oModel.bindList) {
        var oListBinding = oModel.bindList("/AdjudicationCase");
        oListBinding.requestContexts(0, 200).then(function (aContexts) {
          var aCases = aContexts.map(function (oCtx) {
            return oCtx.getObject();
          });
          that._computeKPIs(aCases);
        }).catch(function () {
          that._loadFromMockData();
        });
      } else {
        // Model not available yet — use mock data directly
        this._loadFromMockData();
      }
    },

    _loadFromMockData: function () {
      var that = this;
      var sUrl = sap.ui.require.toUrl("zpwc/gts/spladjudication/localService/mockdata/AdjudicationCase.json");
      jQuery.ajax({
        url: sUrl,
        dataType: "json",
        success: function (aData) {
          that._computeKPIs(aData);
        },
        error: function () {
          // Last resort: try relative path
          jQuery.ajax({
            url: "localService/mockdata/AdjudicationCase.json",
            dataType: "json",
            success: function (aData) {
              that._computeKPIs(aData);
            }
          });
        }
      });
    },

    _computeKPIs: function (aCases) {
      var aBP = aCases.filter(function (c) { return c.IntakePath === "BP Block"; });
      var aDoc = aCases.filter(function (c) { return c.IntakePath === "Doc Block"; });

      var oData = {
        // Shared summary bar
        totalQueue: aCases.filter(function (c) { return !c.HumanDecision; }).length,
        escalated: aCases.filter(function (c) { return c.DispositionBand === "Escalate" && !c.HumanDecision; }).length,
        pendingReview: aCases.filter(function (c) { return c.Status === "New"; }).length,
        clearedToday: aCases.filter(function (c) { return c.HumanDecision === "Confirmed"; }).length,

        bpCount: aBP.length,
        docCount: aDoc.length,

        // BP tab
        bp: this._computeBPKPIs(aBP),

        // Doc tab
        doc: this._computeDocKPIs(aDoc)
      };

      this._oDashboardModel.setData(oData);
    },

    _computeBPKPIs: function (aCases) {
      var aActive = aCases.filter(function (c) { return !c.HumanDecision; });

      return {
        escalateCount: aActive.filter(function (c) { return c.DispositionBand === "Escalate"; }).length,
        reviewCount: aActive.filter(function (c) { return c.DispositionBand === "Review"; }).length,
        propClearCount: aActive.filter(function (c) { return c.DispositionBand === "Propose Clear"; }).length,
        autoClearCount: aActive.filter(function (c) { return c.DispositionBand === "Auto-clear"; }).length,

        programmes: this._countBy(aActive, "SPLProgramme"),
        countries: this._countBy(aActive, "BPCountry"),

        age: {
          fresh: aActive.filter(function (c) { return c.AgedDays <= 1; }).length,
          normal: aActive.filter(function (c) { return c.AgedDays >= 2 && c.AgedDays <= 3; }).length,
          aging: aActive.filter(function (c) { return c.AgedDays >= 4 && c.AgedDays <= 7; }).length,
          old: aActive.filter(function (c) { return c.AgedDays > 7; }).length
        },

        unassigned: aActive.filter(function (c) { return !c.AssignedTo; }).length,

        recent: aCases.sort(function (a, b) {
          return (b.CreatedAt || "").localeCompare(a.CreatedAt || "");
        }).slice(0, 4)
      };
    },

    _computeDocKPIs: function (aCases) {
      var aActive = aCases.filter(function (c) { return !c.HumanDecision; });

      var nTotalValue = aActive.reduce(function (sum, c) { return sum + (c.OrderValue || 0); }, 0);
      var sCurrency = "USD";
      if (aActive.length && aActive[0].OrderCurrency) {
        sCurrency = aActive[0].OrderCurrency;
      }

      var nTotalK = Math.round(nTotalValue / 1000);

      return {
        totalValue: nTotalK + "K",
        valueCurrency: sCurrency,
        highValueCount: aActive.filter(function (c) { return c.OrderValue >= 100000; }).length,

        escalateCount: aActive.filter(function (c) { return c.DispositionBand === "Escalate"; }).length,
        reviewCount: aActive.filter(function (c) { return c.DispositionBand === "Review"; }).length,
        propClearCount: aActive.filter(function (c) { return c.DispositionBand === "Propose Clear"; }).length,

        shipToCountries: this._countByWithValue(aActive, "ShipToCountry", "OrderValue"),
        docTypes: this._countBy(aActive, "DocumentType"),
        programmes: this._countBy(aActive, "SPLProgramme"),

        recent: aCases.sort(function (a, b) {
          return (b.CreatedAt || "").localeCompare(a.CreatedAt || "");
        }).slice(0, 4)
      };
    },

    _countBy: function (aCases, sField) {
      var oCounts = {};
      aCases.forEach(function (c) {
        var sVal = c[sField] || "(blank)";
        oCounts[sVal] = (oCounts[sVal] || 0) + 1;
      });
      return Object.keys(oCounts).map(function (k) {
        return { name: k, count: oCounts[k] };
      }).sort(function (a, b) { return b.count - a.count; });
    },

    _countByWithValue: function (aCases, sField, sValueField) {
      var oAgg = {};
      aCases.forEach(function (c) {
        var sVal = c[sField] || "(blank)";
        if (!oAgg[sVal]) oAgg[sVal] = { count: 0, value: 0 };
        oAgg[sVal].count++;
        oAgg[sVal].value += (c[sValueField] || 0);
      });
      return Object.keys(oAgg).map(function (k) {
        return {
          name: k,
          count: oAgg[k].count,
          value: Math.round(oAgg[k].value / 1000) + "K"
        };
      }).sort(function (a, b) { return b.count - a.count; });
    },

    // ──────────────── Navigation ────────────────

    onNavigateToList: function () {
      this._navToList();
    },

    onNavigateToDocList: function () {
      this._navToList({ IntakePath: "Doc Block" });
    },

    onTilePress: function (oEvent) {
      var oTile = oEvent.getSource();
      var aCustomData = oTile.getCustomData();
      if (aCustomData.length > 0) {
        var sFilter = aCustomData[0].getValue();
        var aParts = sFilter.split("=");
        var oFilter = {};
        oFilter[aParts[0]] = aParts[1];
        this._navToList(oFilter);
      }
    },

    onProgrammePress: function (oEvent) {
      var oItem = oEvent.getSource();
      var oCtx = oItem.getBindingContext("dashboard");
      this._navToList({ SPLProgramme: oCtx.getProperty("name") });
    },

    onCountryPress: function (oEvent) {
      var oItem = oEvent.getSource();
      var oCtx = oItem.getBindingContext("dashboard");
      this._navToList({ BPCountry: oCtx.getProperty("name") });
    },

    onShipToPress: function (oEvent) {
      var oItem = oEvent.getSource();
      var oCtx = oItem.getBindingContext("dashboard");
      this._navToList({ ShipToCountry: oCtx.getProperty("name"), IntakePath: "Doc Block" });
    },

    onCasePress: function (oEvent) {
      var oItem = oEvent.getSource();
      var oCtx = oItem.getBindingContext("dashboard");
      var sCaseUUID = oCtx.getProperty("CaseUUID");
      if (sCaseUUID) {
        this.getOwnerComponent().getRouter().navTo("AdjudicationCaseObjectPage", {
          key: sCaseUUID
        });
      }
    },

    onTabSelect: function () {
      // Tab switch is handled by the IconTabBar binding; nothing extra needed
    },

    _navToList: function (oFilters) {
      // In an FPM custom page, the router lives on the shell or the root component.
      // Try multiple resolution paths to find a working router.
      var oRouter = this.getOwnerComponent() && this.getOwnerComponent().getRouter();
      if (!oRouter) {
        // Fallback: navigate via hash directly
        window.hasher = window.hasher || { setHash: function (h) { window.location.hash = h; } };
      }

      if (!oFilters) {
        if (oRouter) {
          oRouter.navTo("AdjudicationCaseList");
        } else {
          window.location.hash = "#/cases";
        }
        return;
      }

      // Build the hash with encoded filters
      var sQuery = Object.keys(oFilters).map(function (k) {
        return k + "=" + encodeURIComponent(oFilters[k]);
      }).join("&");

      if (oRouter) {
        oRouter.navTo("AdjudicationCaseList", { "?query": { filters: sQuery } });
      } else {
        window.location.hash = "#/cases?filters=" + encodeURIComponent(sQuery);
      }
    }

  });
});
