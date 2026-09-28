"use client";

import {
  useEffect,
  useState,
} from "react";

import {
  useRouter,
} from "next/navigation";

import styles from "./DraftActionController.module.css";


type GatewayPrincipal = {
  is_authenticated: boolean;
  role: "public" | "manager";
  franchise_id: number | null;
  team_key: string | null;
  team_name: string | null;
  display_name: string;
};


type ApiProblem = {
  detail?: {
    code?: string;
  };
};


type Props = {
  draftKey: string;
  currentPickId: string;
  currentOwnerTeamKey: string;
};


const BUTTON_SELECTOR =
  "[data-mlf-draft-player]";


function problemMessage(
  code: string | undefined,
): string {
  switch (code) {
    case "authentication_required":
      return (
        "Manager access is required."
      );

    case "pick_owner_forbidden":
      return (
        "This pick belongs to another team."
      );

    case "invalid_origin":
      return (
        "The request was rejected by "
        + "same-origin protection."
      );

    case "draft_conflict":
      return (
        "Draft state changed before "
        + "the pick completed."
      );

    case "not_found":
    case "draft_not_found":
      return (
        "The draft or player could "
        + "not be found."
      );

    case "service_unavailable":
      return (
        "The draft service is temporarily "
        + "unavailable."
      );

    default:
      return (
        "The pick could not be submitted."
      );
  }
}


export function DraftActionController({
  draftKey,
  currentPickId,
  currentOwnerTeamKey,
}: Props) {
  const router =
    useRouter();

  const [
    authorized,
    setAuthorized,
  ] = useState(false);

  const [
    statusText,
    setStatusText,
  ] = useState(
    "Checking manager access...",
  );

  const [
    submitting,
    setSubmitting,
  ] = useState(false);

  useEffect(
    () => {
      let cancelled =
        false;

      async function loadPrincipal() {
        setAuthorized(false);

        try {
          const response =
            await fetch(
              "/api/mlf/auth/me",
              {
                method: "GET",
                credentials:
                  "same-origin",
                cache: "no-store",
              },
            );

          if (!response.ok) {
            throw new Error(
              "auth unavailable",
            );
          }

          const principal =
            (
              await response.json()
            ) as GatewayPrincipal;

          if (cancelled) {
            return;
          }

          if (
            !principal.is_authenticated
            || principal.role
              !== "manager"
            || !principal.team_key
          ) {
            setStatusText(
              "Public view — use your "
              + "private team link to draft.",
            );

            return;
          }

          if (
            principal.team_key
            !== currentOwnerTeamKey
          ) {
            setStatusText(
              `Signed in as ${
                principal.team_name
                ?? principal.display_name
              } · waiting for another team.`,
            );

            return;
          }

          setAuthorized(true);

          setStatusText(
            `On the clock · ${
              principal.team_name
              ?? principal.display_name
            } · pick ${currentPickId}`,
          );
        } catch {
          if (!cancelled) {
            setStatusText(
              "Manager gateway is "
              + "currently unavailable.",
            );
          }
        }
      }

      void loadPrincipal();

      return () => {
        cancelled = true;
      };
    },
    [
      currentOwnerTeamKey,
      currentPickId,
    ],
  );

  useEffect(
    () => {
      const buttons =
        Array.from(
          document.querySelectorAll<
            HTMLButtonElement
          >(
            BUTTON_SELECTOR,
          ),
        );

      for (
        const button
        of buttons
      ) {
        button.disabled =
          !authorized
          || submitting;
      }
    },
    [
      authorized,
      submitting,
      currentPickId,
    ],
  );

  useEffect(
    () => {
      async function onClick(
        event: MouseEvent,
      ) {
        const target =
          event.target;

        if (
          !(target instanceof Element)
        ) {
          return;
        }

        const button =
          target.closest<
            HTMLButtonElement
          >(
            BUTTON_SELECTOR,
          );

        if (
          !button
          || !authorized
          || submitting
        ) {
          return;
        }

        const playerKey =
          button.dataset
            .mlfDraftPlayer
          ?? "";

        const playerName =
          button.dataset
            .mlfDraftPlayerName
          ?? playerKey;

        if (!playerKey) {
          return;
        }

        event.preventDefault();

        const confirmed =
          window.confirm(
            `Draft ${playerName} `
            + `with pick ${currentPickId}?`,
          );

        if (!confirmed) {
          return;
        }

        setSubmitting(true);

        setStatusText(
          `Submitting ${playerName}...`,
        );

        try {
          const response =
            await fetch(
              `/api/mlf/drafts/${encodeURIComponent(draftKey)}/picks`,
              {
                method: "POST",
                credentials:
                  "same-origin",
                headers: {
                  "Content-Type":
                    "application/json",
                },
                body: JSON.stringify({
                  pick_id:
                    currentPickId,
                  expected_owner_team_key:
                    currentOwnerTeamKey,
                  yahoo_player_key:
                    playerKey,
                }),
              },
            );

          const payload =
            (
              await response.json()
            ) as ApiProblem;

          if (!response.ok) {
            const code =
              payload.detail?.code;

            setStatusText(
              problemMessage(
                code,
              ),
            );

            if (
              response.status
              === 409
            ) {
              router.refresh();
            }

            return;
          }

          setStatusText(
            `Drafted ${playerName}. `
            + "Refreshing...",
          );

          router.refresh();
        } catch {
          setStatusText(
            "The draft service is "
            + "temporarily unavailable.",
          );
        } finally {
          setSubmitting(false);
        }
      }

      document.addEventListener(
        "click",
        onClick,
      );

      return () => {
        document.removeEventListener(
          "click",
          onClick,
        );
      };
    },
    [
      authorized,
      submitting,
      currentPickId,
      currentOwnerTeamKey,
      draftKey,
      router,
    ],
  );

  return (
    <section
      className={
        authorized
          ? `${styles.panel} ${styles.panelActive}`
          : styles.panel
      }
      data-draft-action-controller
    >
      <span
        className={
          styles.label
        }
      >
        Draft Control
      </span>

      <strong>
        {statusText}
      </strong>
    </section>
  );
}
