export const NEW_LEAGUE_STORAGE_KEY =
  "commissioner-tools:new-league";

export type LeagueSetupDraft = {
  leagueName: string;
  sport: string;
  platform: string;
  seasonYear: number;
  managerCount: number;

  leagueModel: string;

  keeperCount: number;
  keeperCostMode: string;

  contractDurations: number[];
  restrictedRights: boolean;
  restrictedRightsLabel: string;
  prospectDesignation: boolean;
  franchiseDesignation: boolean;
  futurePickTrading: boolean;
  annualDraft: boolean;

  draftMethod: string;
  executionMode: "offline";
  startingBudget: number;
  minimumBid: number;
};
