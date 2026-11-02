// The servers page: every dedicated server that published its status, online or not.

import { $, apiUrl, getJSON, setTitle, showServers, start } from "./portal.js";

start(async ({ game }) => {
  setTitle(game, "Servers");
  $("servers-game").textContent = `${game.name} dedicated`;
  showServers(game, await getJSON(apiUrl(game, "/servers")));
});
