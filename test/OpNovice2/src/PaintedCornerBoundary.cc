// Project-side, opt-in numerical candidate. Geant4 installation is unchanged.
#include "PaintedCornerBoundary.hh"
#include "PaintedCornerRule.hh"
#include "DetectorConstruction.hh"
#include "OpticalNumerics.hh"
#include "G4Box.hh"
#include "G4Event.hh"
#include "G4GeometryTolerance.hh"
#include "G4LogicalBorderSurface.hh"
#include "G4LogicalVolume.hh"
#include "G4Navigator.hh"
#include "G4ParticleChange.hh"
#include "G4OpticalPhoton.hh"
#include "G4ProcessManager.hh"
#include "G4Step.hh"
#include "G4TouchableHistory.hh"
#include "G4Track.hh"
#include "G4TransportationManager.hh"
#include "G4VPhysicalVolume.hh"
#include <cmath>

PaintedCornerBoundary::PaintedCornerBoundary(const DetectorConstruction* detector)
 : G4OpBoundaryProcess("OpBoundary"), fDetector(detector) {}

G4VParticleChange* PaintedCornerBoundary::PostStepDoIt(const G4Track& track, const G4Step& step)
{
  // Always let the standard process perform reflection and normal back-relocation.
  auto* result = G4OpBoundaryProcess::PostStepDoIt(track, step);
  auto* change = dynamic_cast<G4ParticleChange*>(result);
  const auto* event = G4RunManager::GetRunManager()->GetCurrentEvent();
  const auto eventID = event ? event->GetEventID() : -1;
  if (eventID != fEvent) { fPending.clear(); fLastCorrection.clear(); fEvent=eventID; }
  const auto id = track.GetTrackID(), number = track.GetCurrentStepNumber();
  const auto found = fPending.find(id);
  if (found != fPending.end()) {
    const auto pending = found->second;
    fPending.erase(found); // Consumed once, including nonmatching next steps.
    if (number == pending.step+1 && GetStatus() == StepTooSmall
        && track.GetTrackStatus() == fAlive && result->GetTrackStatus() == fAlive) {
      auto* navigator = G4TransportationManager::GetTransportationManager()->GetNavigatorForTracking();
      const auto navTouchable = navigator->CreateTouchableHistoryHandle();
      const auto nextTouchable = track.GetNextTouchableHandle();
      const auto* post = step.GetPostStepPoint();
      auto* located=post->GetPhysicalVolume();
      if (!change || !nextTouchable || !located || nextTouchable->GetVolume()!=located
          || navTouchable->GetVolume()!=located || post->GetMaterial()!=located->GetLogicalVolume()->GetMaterial()) {
        OpticalNumerics::Fail("StepTooSmall did not consistently return post/next/navigator/material to the incident tile");
        return result;
      }
      // A consistent relocation elsewhere does not satisfy the candidate's
      // narrow trigger. Preserve its outcome and let the promotion gate reject it.
      if (located != pending.tile) return result;
      if (fLastCorrection.count(id) && fLastCorrection.at(id) == pending.step) {
        OpticalNumerics::Fail("Duplicate correction of one reflection"); return result;
      }
      const auto tolerance = G4GeometryTolerance::GetInstance()->GetSurfaceTolerance();
      const auto scale = OpticalNumerics::Instance().Scale();
      const auto before = *change->GetPosition();
      auto local = pending.toLocal.TransformPoint(before);
      for (G4int axis=0; axis<3; ++axis) {
        if (pending.faces & (1 << axis)) {
          if (std::abs(std::abs(local[axis])-pending.halfSize[axis]) > tolerance) {
            OpticalNumerics::Fail("Back-relocation point left the registered near-face tolerance"); return result;
          }

        }
      }
      const auto inset = PaintedCornerRule::Inset({local.x(),local.y(),local.z()},
        {pending.halfSize.x(),pending.halfSize.y(),pending.halfSize.z()}, tolerance,scale,pending.faces);
      local = {inset[0],inset[1],inset[2]};
      const auto after = pending.toLocal.Inverse().TransformPoint(local);
      auto* solid = pending.tile->GetLogicalVolume()->GetSolid();
      if (solid->Inside(local) != kInside || solid->DistanceToOut(local) <= tolerance
          || (after-before).mag() > std::sqrt(3.)*(scale+1.)*tolerance
          || pending.tile->GetLogicalVolume()->GetNoDaughters() != 0) {
        OpticalNumerics::Fail("Candidate displacement is unsafe, outside the same tile, or exceeds its bound"); return result;
      }
      const auto momentum=*change->GetMomentumDirection(), polarization=*change->GetPolarization();
      const auto energy=change->GetEnergy(), weight=change->GetParentWeight();
      const auto globalTime=change->GetGlobalTime(), localTime=change->GetLocalTime(), properTime=change->GetProperTime();
      const auto deposit=change->GetLocalEnergyDeposit(), path=change->GetTrueStepLength();
      const auto status=change->GetTrackStatus(); const auto secondaries=change->GetNumberOfSecondaries();
      // Same hierarchy/material/touchable: do not alter Transportation's cached handle.
      navigator->LocateGlobalPointWithinVolume(after);
      if (navigator->CreateTouchableHistoryHandle()->GetVolume() != pending.tile
          || (navigator->GetCurrentLocalCoordinate()-local).mag() > tolerance) {
        OpticalNumerics::Fail("Navigator disagrees after same-volume local relocation"); return result;
      }
      change->ProposePosition(after); // The only ParticleChange property modified here.
      if (*change->GetMomentumDirection()!=momentum || *change->GetPolarization()!=polarization
          || change->GetEnergy()!=energy || change->GetParentWeight()!=weight || change->GetGlobalTime()!=globalTime
          || change->GetLocalTime()!=localTime || change->GetProperTime()!=properTime
          || change->GetLocalEnergyDeposit()!=deposit || change->GetTrueStepLength()!=path
          || change->GetTrackStatus()!=status || change->GetNumberOfSecondaries()!=secondaries) {
        OpticalNumerics::Fail("Local relocation changed a forbidden ParticleChange field"); return result;
      }
      fLastCorrection[id] = pending.step;
      OpticalNumerics::Instance().Correction(track, step, pending.step, before, after,
                                             pending.faces, pending.tile->GetCopyNo(), tolerance);
    }
  }
  if (GetStatus() != SpikeReflection || track.GetTrackStatus() != fAlive
      || result->GetTrackStatus() != fAlive || step.GetPostStepPoint()->GetStepStatus() != fGeomBoundary) return result;
  auto* tile = step.GetPreStepPoint()->GetPhysicalVolume();
  if (fDetector->GetTileLayer(tile) < 0) return result;
  auto* border = G4LogicalBorderSurface::GetSurface(tile, step.GetPostStepPoint()->GetPhysicalVolume());
  auto* surface = border ? dynamic_cast<G4OpticalSurface*>(border->GetSurfaceProperty()) : nullptr;
  if (!surface || surface != fDetector->GetSurface() || surface->GetFinish() != polishedfrontpainted) return result;
  const auto* box = dynamic_cast<G4Box*>(tile->GetLogicalVolume()->GetSolid());
  const auto* touch = dynamic_cast<const G4TouchableHistory*>(step.GetPreStepPoint()->GetTouchable());
  if (!box || !touch) { OpticalNumerics::Fail("Candidate requires the unchanged box tile and its touchable history"); return result; }
  const auto transform = touch->GetHistory()->GetTopTransform();
  const auto local = transform.TransformPoint(step.GetPostStepPoint()->GetPosition());
  const G4ThreeVector half(box->GetXHalfLength(), box->GetYHalfLength(), box->GetZHalfLength());
  const auto tolerance = G4GeometryTolerance::GetInstance()->GetSurfaceTolerance();
  const auto faces=PaintedCornerRule::Faces({local.x(),local.y(),local.z()},
                                           {half.x(),half.y(),half.z()},tolerance);
  if (faces && OpticalNumerics::Instance().Mode()=="painted-corner-v2") {
    if (!change || (fLastCorrection.count(id) && fLastCorrection.at(id)==number)) {
      OpticalNumerics::Fail("Missing stock ParticleChange or duplicate immediate correction"); return result;
    }
    const auto scale=OpticalNumerics::Instance().Scale();
    const auto inset=PaintedCornerRule::Inset({local.x(),local.y(),local.z()},
                                            {half.x(),half.y(),half.z()},tolerance,scale,faces);
    const G4ThreeVector inside(inset[0],inset[1],inset[2]);
    const auto position=transform.Inverse().TransformPoint(inside);
    if (box->Inside(inside)!=kInside || box->DistanceToOut(inside)<=tolerance
        || (position-*change->GetPosition()).mag()>std::sqrt(3.)*(scale+1.)*tolerance
        || tile->GetLogicalVolume()->GetNoDaughters()!=0) {
      OpticalNumerics::Fail("Immediate candidate is not strictly inside its unchanged tile"); return result;
    }
    auto* navigator=G4TransportationManager::GetTransportationManager()->GetNavigatorForTracking();
    const auto direction=*change->GetMomentumDirection();
    auto* foundVolume=navigator->LocateGlobalPointAndSetup(position,&direction,false,false);
    const auto relocated=navigator->CreateTouchableHistoryHandle();
    if (foundVolume!=tile || relocated->GetVolume()!=tile
        || (navigator->GetCurrentLocalCoordinate()-inside).mag()>tolerance) {
      OpticalNumerics::Fail("Immediate navigator relocation disagrees with the incident tile"); return result;
    }
    CornerTransportation* transport=nullptr;
    auto* processes=G4OpticalPhoton::OpticalPhoton()->GetProcessManager()->GetProcessList();
    for(int i=0;i<processes->entries();++i)
      if (auto* candidate=dynamic_cast<CornerTransportation*>((*processes)[i])) transport=candidate;
    if (!transport) { OpticalNumerics::Fail("Immediate correction lacks compatible optical Transportation"); return result; }
    transport->AdoptRelocation(relocated,position);
    change->ProposePosition(position); // Stock physical proposals are otherwise untouched.
    fCornerChange.Prepare(track,step,result,relocated);
    fLastCorrection[id]=number;
    OpticalNumerics::Instance().ImmediateCorrection(track,step,position,faces,tolerance);
    return &fCornerChange;
  }
  if (faces) fPending.emplace(id, Pending{number,tile,transform,half,faces});
  return result;
}
