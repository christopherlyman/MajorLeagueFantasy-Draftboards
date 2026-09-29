"use client";

import {
  useState,
} from "react";


type Props = {
  url: string;
  className?: string;
};


export function CopyManagerLinkButton({
  url,
  className,
}: Props) {
  const [
    label,
    setLabel,
  ] = useState("Copy Manager Link");

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(
        url,
      );

      setLabel("Copied");

      window.setTimeout(
        () => setLabel("Copy Manager Link"),
        1600,
      );
    } catch {
      setLabel("Copy failed");
    }
  }

  return (
    <button
      type="button"
      className={className}
      onClick={copyLink}
    >
      {label}
    </button>
  );
}
