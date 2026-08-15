SPL Hit Adjudication - interactive demo
=======================================

The real Fiori application, running on demo data. Filtering, sorting, search,
the saved views and the drill-down to the evidence ledger all work.

There is no server and no SAP system behind it: an in-browser Service Worker
answers the OData V4 requests from the JSON files in localService/mockdata.

IMPORTANT - it must be served over http(s), not opened from disk
---------------------------------------------------------------
Service Workers are blocked on file:// URLs, so double-clicking index.html
will show an explanatory message instead of the app.

To run it locally, from inside this folder:

    npx serve .                     (then open the URL it prints)
  or
    python -m http.server 8080      (then open http://localhost:8080)

To host it, upload this whole folder to any static https host and open
index.html. It works at any path depth. It needs internet access for the
SAPUI5 runtime, which loads from https://ui5.sap.com.

Known-unsuitable host: SharePoint document libraries generally serve HTML as a
download rather than running it, and block Service Workers. Use a proper static
web host instead.

The data is illustrative - invented business partners and SPL entries, not real
screening results.
