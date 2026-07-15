#include "EventAction.hh"

#include "Run.hh"

#include "G4AnalysisManager.hh"
#include "G4Event.hh"
#include "G4PrimaryParticle.hh"
#include "G4PrimaryVertex.hh"
#include "G4RunManager.hh"
#include "G4SystemOfUnits.hh"
#include "G4ThreeVector.hh"

#include <cmath>
#include <limits>

void EventAction::BeginOfEventAction(const G4Event* event)
{
  auto run = static_cast<Run*>(G4RunManager::GetRunManager()->GetNonConstCurrentRun());
  if (run) {
    run->BeginEvent(event ? event->GetEventID() : -1);
  }
}

void EventAction::EndOfEventAction(const G4Event* event)
{
  auto run = static_cast<Run*>(G4RunManager::GetRunManager()->GetNonConstCurrentRun());
  if (!run) {
    return;
  }

  G4ThreeVector primaryPosition;
  G4double primaryEnergy = std::numeric_limits<G4double>::quiet_NaN();
  if (event->GetNumberOfPrimaryVertex() > 0) {
    auto primaryVertex = event->GetPrimaryVertex(0);
    primaryPosition = primaryVertex->GetPosition();
    run->AddShootPosition(primaryPosition);
    auto primaryParticle = primaryVertex->GetPrimary();
    if (primaryParticle) {
      primaryEnergy = primaryParticle->GetKineticEnergy();
      if (std::isfinite(primaryEnergy)) {
        run->AddPrimaryKineticEnergy(primaryEnergy);
      }
    }
  }

  const G4bool hitValid = run->HasEventHitPosition();
  const G4ThreeVector hitPosition = run->GetEventHitPosition();
  const G4bool scintCentroidValid = run->HasEventScintillationCentroid();
  const G4ThreeVector scintCentroid = run->GetEventScintillationCentroid();
  if (scintCentroidValid) {
    run->AddScintillationCentroid(scintCentroid);
  }

  const G4int generatedOptical = run->GetEventGeneratedOpticalCount();
  const G4int cerenkov = run->GetEventCerenkovCount();
  const G4int scintillation = run->GetEventScintillationCount();
  const G4int sipmDetected = run->GetEventSiPMDetectionCount();
  const G4bool collectionEfficiencyValid = generatedOptical > 0;
  const G4double collectionEfficiency =
    collectionEfficiencyValid
      ? G4double(sipmDetected) / G4double(generatedOptical)
      : std::numeric_limits<G4double>::quiet_NaN();
  const G4double missingPosition = std::numeric_limits<G4double>::quiet_NaN();

  const G4double steelEnergyDeposit = run->GetEventSteelEnergyDeposit();
  const G4int neutronElasticCount = run->GetEventPrimaryNeutronElasticCount();
  const G4int neutronInelasticCount = run->GetEventPrimaryNeutronInelasticCount();
  const G4int neutronCaptureCount = run->GetEventPrimaryNeutronCaptureCount();
  const G4bool neutronTileEntryValid = run->HasEventPrimaryNeutronTileEntry();
  const G4ThreeVector neutronTileEntryPosition =
    run->GetEventPrimaryNeutronTileEntryPosition();

  auto analysisMan = G4AnalysisManager::Instance();
  analysisMan->FillNtupleIColumn(0, event->GetEventID());
  analysisMan->FillNtupleDColumn(1, primaryPosition.x() / mm);
  analysisMan->FillNtupleDColumn(2, primaryPosition.y() / mm);
  analysisMan->FillNtupleDColumn(3, primaryPosition.z() / mm);
  analysisMan->FillNtupleIColumn(4, hitValid ? 1 : 0);
  analysisMan->FillNtupleDColumn(5, hitValid ? hitPosition.x() / mm : missingPosition);
  analysisMan->FillNtupleDColumn(6, hitValid ? hitPosition.y() / mm : missingPosition);
  analysisMan->FillNtupleDColumn(7, hitValid ? hitPosition.z() / mm : missingPosition);
  analysisMan->FillNtupleIColumn(8, scintCentroidValid ? 1 : 0);
  analysisMan->FillNtupleDColumn(9, scintCentroidValid ? scintCentroid.x() / mm : missingPosition);
  analysisMan->FillNtupleDColumn(10, scintCentroidValid ? scintCentroid.y() / mm : missingPosition);
  analysisMan->FillNtupleDColumn(11, scintCentroidValid ? scintCentroid.z() / mm : missingPosition);
  analysisMan->FillNtupleIColumn(12, generatedOptical);
  analysisMan->FillNtupleIColumn(13, scintillation);
  analysisMan->FillNtupleIColumn(14, sipmDetected);
  analysisMan->FillNtupleDColumn(15, collectionEfficiency);
  analysisMan->FillNtupleDColumn(16, primaryEnergy / MeV);
  analysisMan->FillNtupleIColumn(17, collectionEfficiencyValid ? 1 : 0);
  analysisMan->FillNtupleIColumn(18, cerenkov);
  analysisMan->FillNtupleDColumn(19, steelEnergyDeposit / MeV);
  analysisMan->FillNtupleIColumn(20, neutronElasticCount);
  analysisMan->FillNtupleIColumn(21, neutronInelasticCount);
  analysisMan->FillNtupleIColumn(22, neutronCaptureCount);
  analysisMan->FillNtupleIColumn(23, neutronElasticCount > 0 ? 1 : 0);
  analysisMan->FillNtupleIColumn(24, neutronInelasticCount > 0 ? 1 : 0);
  analysisMan->FillNtupleIColumn(25, neutronCaptureCount > 0 ? 1 : 0);
  analysisMan->FillNtupleIColumn(
    26,
    neutronElasticCount > 0 || neutronInelasticCount > 0 || neutronCaptureCount > 0
      ? 1
      : 0);
  analysisMan->FillNtupleIColumn(27, run->GetEventChargedTileEntryCount());
  analysisMan->FillNtupleDColumn(
    28, run->GetEventChargedTileEntryKineticEnergy() / MeV);
  analysisMan->FillNtupleIColumn(29, run->GetEventElectronTileEntryCount());
  analysisMan->FillNtupleDColumn(
    30, run->GetEventElectronTileEntryKineticEnergy() / MeV);
  analysisMan->FillNtupleIColumn(31, run->GetEventProtonTileEntryCount());
  analysisMan->FillNtupleDColumn(
    32, run->GetEventProtonTileEntryKineticEnergy() / MeV);
  analysisMan->FillNtupleIColumn(33, run->GetEventOtherChargedTileEntryCount());
  analysisMan->FillNtupleDColumn(
    34, run->GetEventOtherChargedTileEntryKineticEnergy() / MeV);
  analysisMan->FillNtupleIColumn(35, neutronTileEntryValid ? 1 : 0);
  analysisMan->FillNtupleDColumn(
    36, neutronTileEntryValid ? neutronTileEntryPosition.x() / mm : missingPosition);
  analysisMan->FillNtupleDColumn(
    37, neutronTileEntryValid ? neutronTileEntryPosition.y() / mm : missingPosition);
  analysisMan->FillNtupleDColumn(
    38, neutronTileEntryValid ? neutronTileEntryPosition.z() / mm : missingPosition);
  analysisMan->FillNtupleDColumn(39, run->GetEventTileEnergyDeposit() / MeV);
  analysisMan->FillNtupleDColumn(
    40, run->GetEventElectronTileEnergyDeposit() / MeV);
  analysisMan->FillNtupleDColumn(
    41, run->GetEventProtonTileEnergyDeposit() / MeV);
  analysisMan->FillNtupleDColumn(
    42, run->GetEventOtherChargedTileEnergyDeposit() / MeV);
  analysisMan->FillNtupleDColumn(
    43, run->GetEventNeutralTileEnergyDeposit() / MeV);
  analysisMan->AddNtupleRow();

  run->CommitEventStatistics();
}
