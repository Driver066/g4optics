#include "CornerTransportation.hh"
#include "OpticalNumerics.hh"
#include "G4LogicalVolume.hh"
#include "G4Navigator.hh"
#include "G4SafetyHelper.hh"
#include "G4VPhysicalVolume.hh"

void CornerTransportation::AdoptRelocation(const G4TouchableHandle& touch,
                                          const G4ThreeVector& position)
{
  if (!touch || !touch->GetVolume()
      || fLinearNavigator->CreateTouchableHistoryHandle()->GetVolume()!=touch->GetVolume()) {
    OpticalNumerics::Fail("Cannot synchronize optical Transportation with navigator"); return;
  }
  fCurrentTouchableHandle=touch;
  fPreviousSftOrigin=position;
  fPreviousSafety=0.; // A conservative cache reset; no extra step limitation.
  fpSafetyHelper->SetCurrentSafety(0.,position);
  fLastStepInVolume=false; // Reflection stays in the incident tile.
}

void CornerParticleChange::Prepare(const G4Track& track,const G4Step& step,
                                  G4VParticleChange* boundary,const G4TouchableHandle& touch)
{
  if (boundary->GetTrackStatus()!=fAlive || boundary->GetNumberOfSecondaries()!=0
      || boundary->GetLocalEnergyDeposit()!=0. || boundary->GetNonIonizingEnergyDeposit()!=0.) {
    OpticalNumerics::Fail("Unexpected physical proposal in a live painted SpikeReflection"); return;
  }
  G4VParticleChange::Initialize(track);
  ProposeTrackStatus(boundary->GetTrackStatus());
  fBoundary=boundary;
  auto* logical=touch->GetVolume()->GetLogicalVolume();
  fGeometry.Initialize(track);
  fGeometry.SetTouchableHandle(touch);
  fGeometry.SetMaterialInTouchable(logical->GetMaterial());
  fGeometry.SetMaterialCutsCoupleInTouchable(logical->GetMaterialCutsCouple());
  fGeometry.SetSensitiveDetectorInTouchable(logical->GetSensitiveDetector());
  fGeometry.ProposeFirstStepInVolume(step.IsFirstStepInVolume());
  fGeometry.ProposeLastStepInVolume(false);
}

G4Step* CornerParticleChange::UpdateStepForPostStep(G4Step* step)
{
  fBoundary->UpdateStepForPostStep(step); // Stock direction, polarization, time, energy, etc.
  const auto original=*step->GetPostStepPoint();
  const auto length=step->GetStepLength(),deposit=step->GetTotalEnergyDeposit();
  fGeometry.UpdateStepForPostStep(step); // Only touchable/material/cuts/SD and volume flags.
  const auto* post=step->GetPostStepPoint();
  if (post->GetMomentumDirection()!=original.GetMomentumDirection()
      || post->GetPolarization()!=original.GetPolarization()
      || post->GetKineticEnergy()!=original.GetKineticEnergy()
      || post->GetGlobalTime()!=original.GetGlobalTime() || post->GetLocalTime()!=original.GetLocalTime()
      || post->GetProperTime()!=original.GetProperTime() || post->GetWeight()!=original.GetWeight()
      || post->GetVelocity()!=original.GetVelocity() || post->GetPosition()!=original.GetPosition()
      || step->GetStepLength()!=length || step->GetTotalEnergyDeposit()!=deposit) {
    OpticalNumerics::Fail("Geometry ParticleChange altered the stock physical boundary outcome");
  }
  fBoundary->Clear();
  return step;
}
