// The account page: sign in or sign up, claim a game profile with its link code, and set its name and clan tag.

import { $, getJSON, num, render, start } from "./portal.js";

/** Sends a change to the account API. Gives the account it answers with, or throws its error message. */
async function post(path, body = {}) {
  const response = await fetch(`/api/account${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  const answer = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(answer.error || `The server answered HTTP ${response.status}.`);
  return answer;
}

function say(text, ok = true) {
  const box = $("account-message");
  box.textContent = text;
  box.classList.toggle("ok", ok);
  box.hidden = !text;
}

/** Runs `action` when the form is sent, then shows the account it gives and the `done` message. */
function whenSent(form, action, done) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const buttons = form.querySelectorAll("button");
    buttons.forEach((b) => (b.disabled = true));
    try {
      const account = await action(Object.fromEntries(new FormData(form)));
      show(account);
      say(typeof done === "function" ? done(account) : done);
      if (form.id) form.reset();
    } catch (error) {
      say(error.message, false);
    } finally {
      buttons.forEach((b) => (b.disabled = false));
    }
  });
}

function profileCard(p) {
  const shown = p.admin_name || p.display_name || p.uniquenick;
  let note = "The game shows a new clan tag at its next sign-in to Prejudice Online.";
  if (p.admin_name) note = `The server admin sets the name shown for this profile: ${p.admin_name}. ${note}`;
  const card = render("tpl-profile", {
    clan: { text: p.clan_tag ? `[${p.clan_tag}]` : "", hidden: !p.clan_tag },
    name: shown,
    nick: `GameSpy profile ${p.profileid} · ${p.uniquenick}`,
    note,
  });
  card.querySelector("[data-field=careers]").replaceChildren(
    ...p.careers.map((c) =>
      render("tpl-career", {
        link: { href: c.url },
        icon: { src: c.icon, alt: c.rank },
        text: `${c.game} · Level ${c.level} · ${num(c.xp)} XP`,
      }),
    ),
  );
  card.elements.display_name.value = p.display_name || "";
  card.elements.clan_tag.value = p.clan_tag || "";
  whenSent(
    card,
    (values) => post(`/profiles/${p.profileid}`, values),
    "Saved. The leaderboard shows the new name now.",
  );
  card.querySelector("[data-field=release]").addEventListener("click", async () => {
    if (!confirm(`Release ${shown}? Any account can then claim it with a new code from the game.`)) return;
    try {
      show(await post(`/profiles/${p.profileid}/release`));
      say(`${shown} is released.`);
    } catch (error) {
      say(error.message, false);
    }
  });
  return card;
}

function show(account) {
  $("signed-out").hidden = account.signed_in;
  $("signed-in").hidden = !account.signed_in;
  if (!account.signed_in) {
    $("account-summary").textContent = "Sign in to claim your game profiles and set their display name and clan tag.";
    return;
  }
  $("account-name").textContent = account.username;
  const count = account.profiles.length;
  $("account-summary").textContent = `${count} claimed profile${count === 1 ? "" : "s"}`;
  $("profiles").replaceChildren(...(count ? account.profiles.map(profileCard) : [render("tpl-no-profiles")]));
}

start(async () => {
  document.title = "Account · Section 8 Stats";
  whenSent($("login-form"), (v) => post("/login", v), "");
  whenSent($("register-form"), (v) => post("/register", v), "Account created. Claim your profile with the code from the game.");
  whenSent(
    $("claim-form"),
    (v) => post("/claim", v),
    (account) => {
      const p = account.profiles.find((x) => x.profileid === account.claimed);
      return `${p ? p.uniquenick : "The profile"} is now yours. At its next sign-in, the game shows it as activated.`;
    },
  );
  whenSent($("password-form"), (v) => post("/password", v), "Password changed. Your other sessions are signed out.");
  $("logout").addEventListener("click", async () => {
    try {
      show(await post("/logout"));
      say("");
    } catch (error) {
      say(error.message, false);
    }
  });
  show(await getJSON("/api/account"));
});
